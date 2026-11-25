"""Lua scripts: each check-and-update is a single atomic step on the Redis server, so
concurrent gateway replicas can never double-spend a budget or miss a loop. Time comes
from the Redis server (TIME), not from replica clocks.
"""

from __future__ import annotations

from typing import Final

# KEYS: breaker, loop window (zset), loop index (set)
# ARGV: enabled, window_ms, max_identical, cooldown_s (0 = manual reset only),
#       member (unique per call), trip document (JSON), fingerprint, index_ttl_s
# Returns {state, detail, extra}:
#   {"open", <trip json>, <pttl ms, -1 when no expiry>}
#   {"tripped", <trip json>, <identical calls>}
#   {"closed", <identical calls>, "0"}
LOOP_CHECK: Final = """
local trip = redis.call('GET', KEYS[1])
if trip then
  return {'open', trip, tostring(redis.call('PTTL', KEYS[1]))}
end
if ARGV[1] ~= '1' then
  return {'closed', '0', '0'}
end
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now - window)
redis.call('ZADD', KEYS[2], now, ARGV[5])
redis.call('PEXPIRE', KEYS[2], window)
redis.call('SADD', KEYS[3], ARGV[7])
redis.call('EXPIRE', KEYS[3], tonumber(ARGV[8]))
local count = redis.call('ZCARD', KEYS[2])
if count > tonumber(ARGV[3]) then
  local cooldown = tonumber(ARGV[4])
  if cooldown > 0 then
    redis.call('SET', KEYS[1], ARGV[6], 'EX', cooldown)
  else
    redis.call('SET', KEYS[1], ARGV[6])
  end
  return {'tripped', ARGV[6], tostring(count)}
end
return {'closed', tostring(count), '0'}
"""

# KEYS: breaker, loop index; ARGV: loop key prefix. Returns 1 if a breaker was open.
BREAKER_RESET: Final = """
local was_open = redis.call('DEL', KEYS[1])
for _, fingerprint in ipairs(redis.call('SMEMBERS', KEYS[2])) do
  redis.call('DEL', ARGV[1] .. fingerprint)
end
redis.call('DEL', KEYS[2])
return was_open
"""

# KEYS: budget hash
# ARGV: max_tool_calls, max_total_tokens, max_cost_micros, call_cost_micros, ttl_s
# Returns {status, tool_calls, total_tokens, cost_micros}; status is "ok" or the name of
# the limit that refused the call (nothing is charged when refused).
BUDGET_CHARGE_TOOL_CALL: Final = """
local calls = tonumber(redis.call('HGET', KEYS[1], 'tool_calls') or '0')
local tokens = tonumber(redis.call('HGET', KEYS[1], 'total_tokens') or '0')
local cost = tonumber(redis.call('HGET', KEYS[1], 'cost_micros') or '0')
local add = tonumber(ARGV[4])
if calls >= tonumber(ARGV[1]) then
  return {'max_tool_calls', calls, tokens, cost}
end
if tokens >= tonumber(ARGV[2]) then
  return {'max_total_tokens', calls, tokens, cost}
end
if cost >= tonumber(ARGV[3]) or cost + add > tonumber(ARGV[3]) then
  return {'max_cost_usd', calls, tokens, cost}
end
calls = redis.call('HINCRBY', KEYS[1], 'tool_calls', 1)
cost = redis.call('HINCRBY', KEYS[1], 'cost_micros', add)
redis.call('HINCRBY', KEYS[1], 'tool_cost_micros', add)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[5]))
return {'ok', calls, tokens, cost}
"""

# KEYS: budget hash
# ARGV: input, output, cache_write, cache_read tokens, cost_micros, ttl_s
# Usage has already happened, so it is always recorded. Returns {tool_calls, tokens, cost}.
BUDGET_RECORD_USAGE: Final = """
local total = tonumber(ARGV[1]) + tonumber(ARGV[2]) + tonumber(ARGV[3]) + tonumber(ARGV[4])
redis.call('HINCRBY', KEYS[1], 'input_tokens', ARGV[1])
redis.call('HINCRBY', KEYS[1], 'output_tokens', ARGV[2])
redis.call('HINCRBY', KEYS[1], 'cache_write_tokens', ARGV[3])
redis.call('HINCRBY', KEYS[1], 'cache_read_tokens', ARGV[4])
local tokens = redis.call('HINCRBY', KEYS[1], 'total_tokens', total)
redis.call('HINCRBY', KEYS[1], 'llm_cost_micros', ARGV[5])
local cost = redis.call('HINCRBY', KEYS[1], 'cost_micros', ARGV[5])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[6]))
local calls = tonumber(redis.call('HGET', KEYS[1], 'tool_calls') or '0')
return {calls, tokens, cost}
"""

# KEYS: budget hash; ARGV: call_cost_micros
# Undoes one charge_tool_call for a call the upstream provably never ran (each counter is
# floored at zero, so a refund can never mint budget). Returns the new tool_calls.
BUDGET_REFUND_TOOL_CALL: Final = """
local function sub(field, amount)
  local current = tonumber(redis.call('HGET', KEYS[1], field) or '0')
  local value = math.max(0, current - amount)
  redis.call('HSET', KEYS[1], field, value)
  return value
end
local calls = sub('tool_calls', 1)
sub('cost_micros', tonumber(ARGV[1]))
sub('tool_cost_micros', tonumber(ARGV[1]))
return calls
"""

terraform {
  # 1.11+: write-only arguments, so generated secrets never reach plans or state.
  required_version = ">= 1.11.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.74"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }

  # Partial configuration: nothing environment-specific is committed. Run
  #   terraform init -backend-config=backend.hcl      (see backend.hcl.example)
  backend "s3" {}
}

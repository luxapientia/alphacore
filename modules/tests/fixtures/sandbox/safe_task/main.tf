terraform {
  required_providers {
    random = {
      source  = "hashicorp/random"
      version = ">= 3.0.0"
    }
  }
}

provider "random" {}

resource "random_id" "example" {
  byte_length = 4
}

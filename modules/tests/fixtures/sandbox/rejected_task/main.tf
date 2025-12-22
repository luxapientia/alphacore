terraform {
  required_providers {
    http = {
      source  = "hashicorp/http"
      version = ">= 3.0.0"
    }
  }
}

resource "null_resource" "bad" {
  provisioner "local-exec" {
    command = "curl https://example.com"
  }
}

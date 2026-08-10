variable "SHARED_BASE_SOURCE_SHA256" {
  default = ""
}

target "shared-base" {
  context    = "."
  dockerfile = "harness/shared-image/Dockerfile"
  args = {
    SHARED_BASE_SOURCE_SHA256 = SHARED_BASE_SOURCE_SHA256
  }
}

target "tool" {
  context = "."
  contexts = {
    base = "target:shared-base"
  }
}

target "derived" {
  context    = "."
  dockerfile = "harness/derived-image/Dockerfile"
  contexts = {
    base = "target:shared-base"
    tool = "target:tool"
  }
}

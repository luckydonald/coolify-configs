For the mcp server config, I don't want to hardcode `envmcp` into the command, but instead use a tool definition in the core settings file, to then be written to the configs accordingly and merged.
```json5
{
  // other config stuff
  "mcp": {
    "tools": {
      // key: tool name
      ".env": {
          // key: variant
          // empty key = default variant
          "": {
            "mode": "prefix", // <-- only one mode supported for now
            "cmd": ["npx", "-y", "--env-file", "ai/.env"],
          },
          "repo-root": {
            "mode": "prefix",
            "cmd": ["npx", "-y", "--env-file", "$(git rev-parse --show-toplevel)/.env"],
            // ^ not entirely sure if this is possible - i.e. if that command var thing is actually substituted... - but if not this shall be unused and only remain an example for how to create a second variant.
          },
          "debug": {
              "mode": "prefix",
              "cmd": ["npx", "-y", "mcpipe", "--debug", "--env-file", "ai/.env"]
          }
      },
    },
    "servers": {
      // the actual definitions:
      "bugsink": {
          "enabled": true,  // first
          "type": "stdio",
          "tools": [".env", ".env@repo-root"], // format: `tool@variant`. Note that `".env"` == `".env@"` == `.env@default`. They will be added/executed left first to right array element.
          // notice that we don't manually have to split `"cmd"` in `"command": "npx"` and `"args": ["…",…]`, the tool will.
          "cmd": ["npx", "-y", "bugsink-mcp"],
      },
    },
  }
}
```
Create a jsonschema for it, too, please.

import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "../docs/openapi.json",
  output: { path: "src/lib/api" },
  plugins: [
    "@hey-api/client-fetch",
    { name: "zod", compatibilityVersion: 4 },
    { name: "@hey-api/sdk", validator: { response: "zod" } },
    "@tanstack/react-query",
  ],
});

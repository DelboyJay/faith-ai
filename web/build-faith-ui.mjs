import { build } from "esbuild";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

await build({
  entryPoints: [path.join(__dirname, "src", "main.jsx")],
  bundle: true,
  format: "iife",
  target: ["es2020"],
  outfile: path.join(__dirname, "dist", "faith-ui.js"),
  jsx: "automatic",
  loader: {
    ".js": "jsx",
    ".css": "css",
  },
  minify: true,
  sourcemap: false,
  logLevel: "info",
});

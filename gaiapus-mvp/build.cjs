const { readFile, writeFile, copyFile } = require("node:fs/promises");
const { join } = require("node:path");

async function main() {
  const output = __dirname;
  const input = join(output, "..", "gaiapus-apresentacao-react");
  await Promise.all([
    copyFile(join(input, "react-18.3.1.js"), join(output, "vendor", "react.js")),
    copyFile(join(input, "react-dom-18.3.1.js"), join(output, "vendor", "react-dom.js")),
  ]);
  const jsx = (await readFile(join(output, "App.jsx"), "utf8"))
    .replace(/^import React from "react";\r?\n/m, "")
    .replace("export default function App", "function App");
  const compiler = new Bun.Transpiler({ loader: "jsx", tsconfig: { compilerOptions: { jsx: "react" } } });
  const compiled = compiler.transformSync(jsx);
  const validation = (await readFile(join(output, "validation.js"), "utf8")).replace("export function", "function");
  const bootstrap = `
async function start() {
  const root = document.getElementById("root");
  try {
    const read = async (path) => {
      const response = await fetch(path);
      if (!response.ok) throw new Error("Unavailable: " + path);
      return response.json();
    };
    const manifest = await read("./data/regions.json");
    const results = await Promise.allSettled(manifest.map(async (entry) => {
      const [data, cityGeometry, image] = await Promise.all([
        read(entry.directory + "/field.json"), read(entry.directory + "/city.geojson"),
        fetch(entry.directory + "/scene.png"),
      ]);
      if (!image.ok || !image.headers.get("Content-Type")?.startsWith("image/png")) throw new Error("Missing scene");
      data.image.url = entry.directory + "/scene.png";
      return { ...entry, data, cityGeometry };
    }));
    const packages = validatedPackages(results.filter((item) => item.status === "fulfilled").map((item) => item.value));
    ReactDOM.createRoot(root).render(React.createElement(App, { packages }));
  } catch {
    root.textContent = "Não foi possível carregar as observações. Abra o aplicativo pelo servidor local.";
  }
}
start();`;
  await writeFile(join(output, "app.js"), "(function () {\n" + validation + "\n" + compiled + bootstrap + "\n})();\n");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });

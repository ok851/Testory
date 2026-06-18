import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = join(root, 'node_modules/@tauri-apps/api');
const dest = join(root, 'static/vendor/tauri');

if (!existsSync(src)) {
  console.error('Missing @tauri-apps/api. Run: npm install --cache .npm-cache');
  process.exit(1);
}

function copyJsTree(fromDir, toDir) {
  mkdirSync(toDir, { recursive: true });
  for (const name of readdirSync(fromDir)) {
    const from = join(fromDir, name);
    const to = join(toDir, name);
    const st = statSync(from);
    if (st.isDirectory()) {
      copyJsTree(from, to);
      continue;
    }
    if (name.endsWith('.js')) {
      cpSync(from, to);
    }
  }
}

rmSync(dest, { recursive: true, force: true });
copyJsTree(src, dest);
console.log('Copied @tauri-apps/api JS modules -> static/vendor/tauri');

import { build, context } from 'esbuild';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';

const output = new URL('../app/static/', import.meta.url);
const watch = process.argv.includes('--watch');
await rm(output, { recursive: true, force: true });
await mkdir(new URL('assets/', output), { recursive: true });
const options = {
  entryPoints: ['src/main.jsx'],
  bundle: true,
  minify: !watch,
  sourcemap: watch,
  outfile: new URL('assets/index.js', output).pathname,
  jsx: 'automatic',
  define: { 'process.env.NODE_ENV': JSON.stringify(watch ? 'development' : 'production') },
  target: ['es2022'],
};
const html = (await readFile(new URL('index.html', import.meta.url), 'utf8'))
  .replace('<script type="module" src="/src/main.jsx"></script>',
    '<link rel="stylesheet" href="/assets/index.css"><script type="module" src="/assets/index.js"></script>');
await writeFile(new URL('index.html', output), html);
if (watch) {
  const builder = await context(options);
  await builder.watch();
  console.log('Watching assets. Start uvicorn separately and open its localhost URL.');
} else {
  await build(options);
}

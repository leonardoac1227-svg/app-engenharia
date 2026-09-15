// Verifica sintaxe e importações dos módulos do frontend.
//   node --experimental-vm-modules ferramentas/verificar_js.mjs
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const RAIZ = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const BASE = path.join(RAIZ, 'static', 'js');

const arquivos = [];
(function anda(dir) {
  for (const nome of fs.readdirSync(dir)) {
    const caminho = path.join(dir, nome);
    if (fs.statSync(caminho).isDirectory()) anda(caminho);
    else if (caminho.endsWith('.js')) arquivos.push(caminho);
  }
})(BASE);

let erros = 0;

// --- sintaxe ---
for (const arquivo of arquivos) {
  const codigo = fs.readFileSync(arquivo, 'utf8');
  const relativo = path.relative(RAIZ, arquivo);
  try {
    if (/\b(login|sw)\.js$/.test(arquivo)) new vm.Script(codigo);
    else new vm.SourceTextModule(codigo);
  } catch (erro) {
    erros++;
    console.log(` ERRO  ${relativo} :: ${erro.message}`);
  }
}

// --- importações resolvem? ---
const exportados = new Map();
for (const arquivo of arquivos) {
  const codigo = fs.readFileSync(arquivo, 'utf8');
  const nomes = new Set();
  for (const m of codigo.matchAll(/^export\s+(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z0-9_$]+)/gm)) nomes.add(m[1]);
  for (const m of codigo.matchAll(/^export\s*\{([^}]+)\}/gm)) m[1].split(',').forEach((n) => nomes.add(n.trim().split(/\s+as\s+/).pop()));
  exportados.set(path.resolve(arquivo), nomes);
}

for (const arquivo of arquivos) {
  const codigo = fs.readFileSync(arquivo, 'utf8');
  const relativo = path.relative(RAIZ, arquivo);
  for (const m of codigo.matchAll(/import\s+(?:\*\s+as\s+[A-Za-z0-9_$]+|\{([^}]*)\})\s+from\s+'([^']+)'/gs)) {
    const alvo = path.resolve(path.dirname(arquivo), m[2]);
    if (!fs.existsSync(alvo)) {
      erros++; console.log(` ERRO  ${relativo}: arquivo inexistente ${m[2]}`); continue;
    }
    for (const bruto of (m[1] || '').split(',')) {
      const nome = bruto.trim().split(/\s+as\s+/)[0].trim();
      if (nome && !exportados.get(alvo)?.has(nome)) {
        erros++; console.log(` ERRO  ${relativo}: '${nome}' não é exportado por ${m[2]}`);
      }
    }
  }
}

// --- funções usadas mas não definidas nem importadas (erro comum ao refatorar) ---
console.log(`\n${arquivos.length} módulo(s) · ` + (erros ? `${erros} problema(s)` : 'sintaxe e importações OK'));
process.exit(erros ? 1 : 0);

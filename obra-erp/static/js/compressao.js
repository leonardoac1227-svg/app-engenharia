/* ==========================================================================
   Compressão no aparelho, antes de subir.

   O servidor já comprime tudo que recebe — mas isso economiza só disco. Quem
   está no canteiro com 4G ruim precisa economizar o *upload*: uma foto de
   12 MB some para ~300 KB antes de sair do celular, e o que levaria um minuto
   passa a levar segundos.

   Ganho extra no iPhone: fotos em HEIC são decodificadas pelo próprio Safari
   e saem daqui como JPEG, formato que o servidor entende em qualquer versão.

   Regra de ouro, igual à do backend: na dúvida, manda o arquivo original.
   Nenhuma falha de compressão pode impedir o gestor de anexar a nota.
   ========================================================================== */

const LADO_MAXIMO = 1600;
const QUALIDADE = 0.78;
const MINIMO_PARA_COMPRIMIR = 400 * 1024;   // abaixo disso não compensa o esforço
const TIPOS_COMPRIMIVEIS = ['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif'];

export function formatarBytes(bytes) {
  const valor = Number(bytes) || 0;
  if (valor < 1024) return `${valor} B`;
  if (valor < 1024 * 1024) return `${Math.round(valor / 1024)} KB`;
  return `${(valor / (1024 * 1024)).toFixed(1).replace('.', ',')} MB`;
}

async function carregarBitmap(arquivo) {
  if (typeof createImageBitmap === 'function') {
    try {
      return await createImageBitmap(arquivo);
    } catch (_) { /* HEIC em navegador que não decodifica: cai no <img> */ }
  }

  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(arquivo);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('imagem ilegível')); };
    img.src = url;
  });
}

/**
 * Comprime uma imagem. Devolve sempre um File utilizável — o original, se a
 * compressão falhar ou não valer a pena.
 */
export async function comprimirImagem(arquivo, { lado = LADO_MAXIMO, qualidade = QUALIDADE } = {}) {
  const ehComprimivel = TIPOS_COMPRIMIVEIS.includes(arquivo.type)
    || /\.(jpe?g|png|webp|heic|heif)$/i.test(arquivo.name || '');

  if (!ehComprimivel) return arquivo;
  // Arquivo pequeno já resolvido: mexer nele só gastaria bateria.
  if (arquivo.size < MINIMO_PARA_COMPRIMIR && arquivo.type === 'image/jpeg') return arquivo;

  try {
    const fonte = await carregarBitmap(arquivo);
    const largura = fonte.width;
    const altura = fonte.height;
    if (!largura || !altura) return arquivo;

    const escala = Math.min(1, lado / Math.max(largura, altura));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(largura * escala);
    canvas.height = Math.round(altura * escala);

    const ctx = canvas.getContext('2d');
    // Fundo branco: PNG com transparência viraria preto ao virar JPEG.
    ctx.fillStyle = '#FFFFFF';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(fonte, 0, 0, canvas.width, canvas.height);
    if (fonte.close) fonte.close();

    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', qualidade));
    if (!blob || blob.size >= arquivo.size) return arquivo;

    const nome = (arquivo.name || 'foto').replace(/\.[^.]+$/, '') + '.jpg';
    return new File([blob], nome, { type: 'image/jpeg', lastModified: Date.now() });
  } catch (_) {
    return arquivo;
  }
}

/** Comprime uma lista de arquivos e informa quanto foi economizado. */
export async function comprimirLote(arquivos, opcoes = {}) {
  const originais = Array.from(arquivos);
  const bytesAntes = originais.reduce((soma, a) => soma + a.size, 0);
  const finais = [];

  for (const arquivo of originais) {
    finais.push(await comprimirImagem(arquivo, opcoes));
  }

  const bytesDepois = finais.reduce((soma, a) => soma + a.size, 0);
  return {
    arquivos: finais,
    bytesAntes,
    bytesDepois,
    economizado: Math.max(bytesAntes - bytesDepois, 0),
    houveGanho: bytesAntes - bytesDepois > 100 * 1024,
    resumo: `${formatarBytes(bytesAntes)} → ${formatarBytes(bytesDepois)}`,
  };
}

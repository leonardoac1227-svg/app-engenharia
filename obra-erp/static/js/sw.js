/* ==========================================================================
   Service worker — casca offline do PWA.

   Estratégia: NETWORK-FIRST para os arquivos do app, com o cache como reserva.

   A versão anterior usava cache-first e trazia um problema sério: como o
   navegador só reinstala o service worker quando o próprio sw.js muda em
   bytes, um deploy que alterava só o CSS nunca chegava ao usuário — o app
   servia a versão velha do cache para sempre.

   Com network-first, quem está online sempre recebe o código novo, e quem
   está sem sinal no canteiro continua abrindo o app a partir do cache.
   O VERSAO abaixo deve ser incrementado a cada release: isso troca o nome do
   cache, muda o sw.js em bytes e força a atualização.
   ========================================================================== */

const VERSAO = 18;
const CACHE = `obra-erp-v${VERSAO}`;

const ESTATICOS = [
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/js/api.js',
  '/static/js/util.js',
  '/static/js/estado.js',
  '/static/js/compressao.js',
  '/static/js/navegacao.js',
  '/static/js/views/home.js',
  '/static/js/views/diario.js',
  '/static/js/views/equipe.js',
  '/static/js/views/financeiro.js',
  '/static/js/views/fechamento.js',
  '/static/js/views/ajustes.js',
  '/static/js/views/comum.js',
  '/static/img/icone.svg',
];

self.addEventListener('install', (evento) => {
  evento.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(ESTATICOS))
      .catch(() => null)          // um estático ausente não pode travar a instalação
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (evento) => {
  evento.waitUntil(
    caches.keys()
      .then((chaves) => Promise.all(chaves.filter((c) => c !== CACHE).map((c) => caches.delete(c))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (evento) => {
  const url = new URL(evento.request.url);
  if (evento.request.method !== 'GET' || url.origin !== self.location.origin) return;

  // Dados da obra e anexos nunca saem do cache: valor errado é pior que erro.
  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/arquivos')) return;

  // Estático com ?v=<versão> nunca muda de conteúdo: pode vir do cache na
  // hora, sem tocar a rede. Quando sai uma versão nova, a URL muda junto e o
  // arquivo antigo simplesmente deixa de ser pedido.
  const imutavel = url.pathname.startsWith('/static') && url.searchParams.has('v');

  if (imutavel) {
    evento.respondWith(
      caches.match(evento.request).then((cacheado) => cacheado || fetch(evento.request)
        .then((resposta) => {
          if (resposta.ok) {
            const copia = resposta.clone();
            caches.open(CACHE).then((cache) => cache.put(evento.request, copia));
          }
          return resposta;
        }))
    );
    return;
  }

  evento.respondWith(
    fetch(evento.request)
      .then((resposta) => {
        if (resposta.ok && url.pathname.startsWith('/static')) {
          const copia = resposta.clone();
          caches.open(CACHE).then((cache) => cache.put(evento.request, copia));
        }
        return resposta;
      })
      .catch(() => caches.match(evento.request).then((cacheado) => cacheado || Response.error()))
  );
});

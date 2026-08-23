/* ==========================================================================
   Service worker — casca offline do PWA.
   Estratégia: cache-first para os arquivos estáticos (o app abre mesmo sem
   sinal no canteiro) e network-only para /api (dado de obra nunca vem velho
   sem o usuário saber).
   ========================================================================== */

const CACHE = 'obra-erp-v2';
const ESTATICOS = [
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/js/api.js',
  '/static/js/util.js',
  '/static/js/estado.js',
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
    caches.open(CACHE).then((cache) => cache.addAll(ESTATICOS)).then(() => self.skipWaiting())
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
  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/arquivos')) return;

  evento.respondWith(
    caches.match(evento.request).then((cacheado) => cacheado || fetch(evento.request).then((resposta) => {
      if (resposta.ok && url.pathname.startsWith('/static')) {
        const copia = resposta.clone();
        caches.open(CACHE).then((cache) => cache.put(evento.request, copia));
      }
      return resposta;
    }).catch(() => caches.match('/static/css/style.css')))
  );
});

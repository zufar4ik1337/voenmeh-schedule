// Сервис-воркер: сохраняет сайт и расписание в телефоне, чтобы всё открывалось без интернета.
// Стратегия «сначала сеть»: если интернет есть — берём свежее, нет — показываем сохранённое.
const CACHE = "raspisanie-v1";
const CORE = ["./", "index.html", "data/schedule.js", "manifest.webmanifest",
              "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png", "icons/favicon.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.pathname.endsWith(".ics")) return;                 // календари не кэшируем
  if (url.hostname.endsWith("yandex.ru")) return;            // счётчик тоже
  const isFont = url.hostname.includes("fonts.g");
  e.respondWith(
    isFont
      // шрифты не меняются — сначала из кэша
      ? caches.match(req).then(hit => hit || fetch(req).then(res => { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); return res; }))
      // всё остальное — сначала сеть, при ошибке — кэш
      : fetch(req).then(res => {
          if (res.ok && url.origin === location.origin) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
          return res;
        }).catch(() => caches.match(req).then(hit => hit || caches.match("index.html")))
  );
});

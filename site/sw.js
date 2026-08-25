/* ===========================================================================
   Champipi — service worker (mode terrain / PWA)

   Objectif : que l'application reste utilisable EN FORÊT, là où le réseau est
   faible ou absent. Trois familles de caches, avec trois stratégies :

   1. CACHE_APP  (page + librairies + icônes)
      - librairies/icônes : « cache d'abord » (elles ne changent qu'avec VERSION) ;
      - la PAGE ("./")     : « réseau d'abord avec délai court » — fraîche quand
        le réseau marche, servie du cache après NET_TIMEOUT_MS sinon. C'est la
        page CHIFFRÉE StatiCrypt qui est cachée : le déchiffrement se fait
        ensuite dans le navigateur, il marche donc aussi hors-ligne.
   2. CACHE_DATA (geom_XX.json, hist_XX.json, stations_hist.json)
      - « cache d'abord, rafraîchi en arrière-plan » : les URLs sont versionnées
        (?v=hash) ; si la version demandée diffère de celle en cache, on sert
        l'ancienne tout de suite (jamais bloquant sur réseau lent) et on
        télécharge la nouvelle pour la prochaine fois.
   3. CACHE_TILES (tuiles OpenTopoMap / OSM / Esri)
      - « cache d'abord » + plafond d'entrées ; hors-ligne, une tuile absente
        devient un carreau gris clair (au lieu de l'icône cassée).

   Mise à jour : incrémenter VERSION invalide CACHE_APP + CACHE_DATA ; les
   tuiles (neutres, lourdes à re-télécharger) survivent aux versions.
   =========================================================================== */
"use strict";

const VERSION     = "champipi-v1";
const CACHE_APP   = VERSION + "::app";
const CACHE_DATA  = VERSION + "::data";
const CACHE_TILES = "champipi::tiles";        // volontairement SANS version
const NET_TIMEOUT_MS = 6000;                   // réseau « qui rame » -> cache
const TILES_MAX      = 4000;                   // ~80-120 Mo de fond de carte max

/* Fichiers de base, mis en cache dès l'installation. */
const CORE = [
  "./",
  "manifest.webmanifest",
  "icon-192.png", "icon-512.png", "apple-touch-icon.png",
  "vendor/leaflet.js", "vendor/leaflet.css",
  "vendor/chart.umd.min.js", "vendor/exifr.umd.js",
  "vendor/images/layers.png", "vendor/images/layers-2x.png",
  "vendor/images/marker-icon.png", "vendor/images/marker-icon-2x.png",
  "vendor/images/marker-shadow.png",
];

/* Hôtes des fonds de carte. */
const TILE_HOSTS = ["tile.opentopomap.org", "tile.openstreetmap.org", "server.arcgisonline.com"];
/* Tuile de repli hors-ligne : 1x1 gris clair, étirée par Leaflet. */
const TUILE_VIDE_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGN48ewBAAVoAq/zhBFrAAAAAElFTkSuQmCC";

function estTuile(url) { return TILE_HOSTS.some((h) => url.hostname.endsWith(h)); }
/* Clé de cache d'une tuile : on neutralise le sous-domaine {s} (a/b/c servent
   la MÊME image) pour ne jamais stocker/chercher une tuile en triple. */
function cleTuile(u) { return u.replace(/^https:\/\/[abc]\.tile\./, "https://a.tile."); }
function estData(url) { return /(?:^|\/)(geom_\d+\w*\.json|hist_\d+\w*\.json|stations_hist\.json)$/.test(url.pathname); }

function tuileVide() {
  const bin = atob(TUILE_VIDE_B64), buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return new Response(buf, { status: 200, headers: { "Content-Type": "image/png" } });
}

function fetchAvecDelai(req, ms) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("timeout")), ms);
    fetch(req).then((r) => { clearTimeout(t); resolve(r); },
                   (e) => { clearTimeout(t); reject(e); });
  });
}

/* --------------------------------------------------------------------------
   Installation / activation
   -------------------------------------------------------------------------- */
self.addEventListener("install", (e) => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE_APP);
    // addAll échouerait en bloc à la moindre 404 : on tolère les absences.
    await Promise.allSettled(CORE.map((u) => c.add(u)));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const noms = await caches.keys();
    await Promise.all(noms
      .filter((n) => n.startsWith("champipi") && n !== CACHE_APP && n !== CACHE_DATA && n !== CACHE_TILES)
      .map((n) => caches.delete(n)));
    await self.clients.claim();           // contrôle immédiat, sans recharger
  })());
});

/* --------------------------------------------------------------------------
   Stratégies de réponse
   -------------------------------------------------------------------------- */
async function repTuile(req) {
  const cle = cleTuile(req.url);
  const c = await caches.open(CACHE_TILES);
  const hit = await c.match(cle);
  if (hit) return hit;
  try {
    const r = await fetchAvecDelai(req, 15000);
    if (r && r.ok) {
      await c.put(cle, r.clone());
      limiterTuiles(c);                   // fire-and-forget
    }
    return r;
  } catch (_) {
    return tuileVide();
  }
}

let _cptTuiles = 0;
async function limiterTuiles(c) {
  if ((++_cptTuiles % 50) !== 0) return;  // vérification 1 fois sur 50
  try {
    const keys = await c.keys();
    for (let i = 0; i < keys.length - TILES_MAX; i++) c.delete(keys[i]);
  } catch (_) {}
}

/* Page ("./") : réseau d'abord (fraîcheur), cache après NET_TIMEOUT_MS. */
async function repPage(req) {
  const c = await caches.open(CACHE_APP);
  try {
    const r = await fetchAvecDelai(req, NET_TIMEOUT_MS);
    if (r && r.ok) { await c.put("./", r.clone()); return r; }
    throw new Error("http " + (r && r.status));
  } catch (_) {
    const hit = await c.match("./", { ignoreSearch: true });
    if (hit) return hit;
    return new Response(
      "<html lang='fr'><body style='font-family:system-ui;padding:2em;text-align:center'>" +
      "<h2>📵 Hors-ligne</h2><p>Champipi n'a pas encore été ouvert avec du réseau " +
      "sur cet appareil : aucune copie locale disponible.</p></body></html>",
      { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } });
  }
}

/* Données versionnées : cache d'abord (toute version), MAJ en arrière-plan. */
async function repData(req, e) {
  const c = await caches.open(CACHE_DATA);
  const hit = await c.match(req, { ignoreSearch: true });
  if (hit) {
    if (hit.url !== req.url) e.waitUntil(rafraichirData(c, req));  // version plus récente publiée
    return hit;
  }
  const r = await fetch(req);
  if (r && r.ok) await c.put(req, r.clone());
  return r;
}
async function rafraichirData(c, req) {
  try {
    const r = await fetch(req);
    if (!(r && r.ok)) return;
    // purge des anciennes versions du même fichier avant d'écrire la nouvelle
    const chemin = new URL(req.url).pathname;
    for (const k of await c.keys()) {
      if (new URL(k.url).pathname === chemin) await c.delete(k);
    }
    await c.put(req, r.clone());
  } catch (_) {}
}

/* Fichiers d'application (vendor, icônes, manifest) : cache d'abord. */
async function repApp(req) {
  const c = await caches.open(CACHE_APP);
  const hit = await c.match(req, { ignoreSearch: true });
  if (hit) return hit;
  const r = await fetch(req);
  if (r && r.ok) await c.put(req, r.clone());
  return r;
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  let url;
  try { url = new URL(req.url); } catch (_) { return; }

  if (estTuile(url)) { e.respondWith(repTuile(req)); return; }
  if (url.origin !== self.location.origin) return;   // autres domaines : défaut

  if (req.mode === "navigate") { e.respondWith(repPage(req)); return; }
  if (estData(url)) { e.respondWith(repData(req, e)); return; }
  if (/\/(vendor\/|icon-|apple-touch-icon|manifest\.webmanifest)/.test(url.pathname) ||
      url.pathname.endsWith("/sw.js")) {
    e.respondWith(repApp(req)); return;
  }
});

/* --------------------------------------------------------------------------
   « Préparer ma sortie » : la page envoie la liste de ce qu'il faut mettre en
   cache (page + données + tuiles de la zone visible). Le SW télécharge avec
   une concurrence limitée et renvoie la progression au client demandeur.
   Message attendu : {type:"PRECACHE", page:bool, data:[url], tuiles:[url]}
   Réponses        : {type:"PRECACHE_PROGRESS", fait, total}
                     {type:"PRECACHE_FIN", ok, echecs, total}
   -------------------------------------------------------------------------- */
self.addEventListener("message", (e) => {
  const m = e.data || {};
  if (m.type !== "PRECACHE") return;
  const client = e.source;
  e.waitUntil(precache(m, client));
});

async function precache(m, client) {
  const taches = [];
  if (m.page) {
    taches.push(async () => {
      const c = await caches.open(CACHE_APP);
      const r = await fetch("./", { cache: "no-cache" });
      if (r && r.ok) await c.put("./", r.clone()); else throw 0;
    });
  }
  (m.data || []).forEach((u) => taches.push(async () => {
    const c = await caches.open(CACHE_DATA);
    if (await c.match(u)) return;                       // version exacte déjà là
    const r = await fetch(u);
    if (!(r && r.ok)) throw 0;
    const chemin = new URL(u, self.location.href).pathname;
    for (const k of await c.keys())
      if (new URL(k.url).pathname === chemin) await c.delete(k);
    await c.put(u, r.clone());
  }));
  (m.tuiles || []).forEach((u) => taches.push(async () => {
    const c = await caches.open(CACHE_TILES);
    const cle = cleTuile(u);
    if (await c.match(cle)) return;
    const r = await fetch(u);
    if (!(r && r.ok)) throw 0;
    await c.put(cle, r.clone());
  }));

  const total = taches.length;
  let fait = 0, echecs = 0, idx = 0;
  const dire = (msg) => { try { client && client.postMessage(msg); } catch (_) {} };
  async function ouvrier() {
    while (idx < taches.length) {
      const t = taches[idx++];
      try { await t(); } catch (_) { echecs++; }
      fait++;
      if (fait % 5 === 0 || fait === total) dire({ type: "PRECACHE_PROGRESS", fait, total });
    }
  }
  // Concurrence 5 : assez pour aller vite, poli pour les serveurs de tuiles.
  await Promise.all([1, 2, 3, 4, 5].map(ouvrier));
  dire({ type: "PRECACHE_FIN", ok: total - echecs, echecs, total });
}

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
   3. CACHE_TILES (tuiles OpenTopoMap / OSM / IGN / Esri)
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
const TILE_HOSTS = ["tile.opentopomap.org", "tile.openstreetmap.org", "server.arcgisonline.com", "data.geopf.fr"];
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

/* Page ("./") : réseau d'abord (fraîcheur), cache après NET_TIMEOUT_MS.
   Exception : juste après une mise à jour (message MAJ ci-dessous), la page
   fraîche vient d'être rangée dans le cache — le rechargement la sert
   directement, sans la re-télécharger ni attendre le réseau. */
let _pageFraicheA = 0;
async function repPage(req) {
  const c = await caches.open(CACHE_APP);
  if (Date.now() - _pageFraicheA < 60000) {
    const hit = await c.match("./", { ignoreSearch: true });
    if (hit) return hit;
  }
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

/* Données versionnées : la version EXACTE (?v=hash) demandée par la page est
   servie du cache si on l'a ; sinon réseau d'abord (la page et ses données
   doivent être de la même génération : l'historique s'arrête à hist_fin et la
   prévision inline démarre juste après — servir un hist de la veille creuse un
   trou d'un jour dans la timeline). Une ancienne version n'est servie qu'en
   dernier recours (hors-ligne / réseau qui rame). */
async function repData(req, e) {
  const c = await caches.open(CACHE_DATA);
  const exact = await c.match(req);                       // même version
  if (exact) return exact;
  const ancien = await c.match(req, { ignoreSearch: true }); // autre version
  try {
    const r = await fetchAvecDelai(req, ancien ? NET_TIMEOUT_MS : 60000);
    if (r && r.ok) {
      e.waitUntil(rangerData(c, req, r.clone()));
      return r;
    }
    throw new Error("http " + (r && r.status));
  } catch (_) {
    if (ancien) return ancien;                             // mieux que rien
    throw _;
  }
}
async function rangerData(c, req, r) {
  try {
    // purge des anciennes versions du même fichier avant d'écrire la nouvelle
    const chemin = new URL(req.url).pathname;
    for (const k of await c.keys()) {
      if (new URL(k.url).pathname === chemin) await c.delete(k);
    }
    await c.put(req, r);
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
  if (url.pathname.endsWith("/version.json")) {          // toujours frais, jamais caché
    e.respondWith(fetch(req, { cache: "no-store" })); return;
  }
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
   Message attendu : {type:"PRECACHE", id?, page:bool, data:[url], tuiles:[url]}
   (l'`id` éventuel est renvoyé tel quel dans chaque réponse : la page distingue
   ainsi une préparation de sortie d'une mise à jour de génération)
   Réponses        : {type:"PRECACHE_PROGRESS", fait, total}
                     {type:"PRECACHE_FIN", ok, echecs, total}
   -------------------------------------------------------------------------- */
self.addEventListener("message", (e) => {
  const m = e.data || {};
  const client = e.source;
  if (m.type === "PRECACHE") e.waitUntil(precache(m, client));
  else if (m.type === "MAJ") e.waitUntil(majGeneration(m, client));
});

/* Mise à jour de génération (lancement de la PWA) : la page a lu version.json
   et constaté qu'une génération plus récente est en ligne. On télécharge la
   nouvelle page, puis les nouvelles versions des SEULS fichiers de données dont
   une ancienne version est déjà en cache (= ceux que l'utilisateur utilise :
   inutile d'aspirer 7 Mo de stations_hist s'il n'a jamais ouvert ce calque).
   Message : {type:"MAJ", id, fichiers:[url?v=hash]} → mêmes réponses que PRECACHE,
   plus `page_ok` dans PRECACHE_FIN. */
async function majGeneration(m, client) {
  let data = [];
  try {
    const c = await caches.open(CACHE_DATA);
    const enCache = new Set((await c.keys()).map((k) => new URL(k.url).pathname));
    data = (m.fichiers || []).filter((u) => {
      try { return enCache.has(new URL(u, self.location.href).pathname); } catch (_) { return false; }
    });
  } catch (_) {}
  await precache({ id: m.id, page: true, data, tuiles: [] }, client);
}

async function precache(m, client) {
  const taches = [];
  let pageOk = !m.page;
  if (m.page) {
    taches.push(async () => {
      const c = await caches.open(CACHE_APP);
      const r = await fetch("./", { cache: "no-cache" });
      if (r && r.ok) { await c.put("./", r.clone()); pageOk = true; _pageFraicheA = Date.now(); }
      else throw 0;
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
  const dire = (msg) => { try { if (m.id) msg.id = m.id; client && client.postMessage(msg); } catch (_) {} };
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
  dire({ type: "PRECACHE_FIN", ok: total - echecs, echecs, total, page_ok: pageOk });
}

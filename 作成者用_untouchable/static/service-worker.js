// キャッシュの名前を定義します。バージョンが変わったらこの名前を変更すると、新しいキャッシュが作られます。
const CACHE_NAME = 'checkin-out-management-cache-v1';
// キャッシュするファイルのリストです。
const urlsToCache = [
  '/',
  '/?mode=admin',
  '/static/css/style.css',
  '/static/js/main.js',
  '/static/icons/icon-192x192.png',
  '/static/icons/icon-512x512.png'
];

// 1. インストールイベント：サービスワーカーがブラウザに初めて登録されたときに一度だけ実行されます。
self.addEventListener('install', (event) => {
  // event.waitUntilは、中の処理が終わるまでインストールを待機させる命令です。
  event.waitUntil(
    // caches.openで指定した名前のキャッシュストレージを開きます。
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('Opened cache');
        // addAllで、指定したファイルのリストをすべてキャッシュに追加します。
        return cache.addAll(urlsToCache);
      })
  );
});

// 2. フェッチイベント：ブラウザが何かをリクエスト（例: 画像の読み込み、ページの表示）するたびに発生します。
self.addEventListener('fetch', (event) => {
  // event.respondWithは、ブラウザのリクエストに対して、サービスワーカーが何を返すかを制御する命令です。
  event.respondWith(
    // caches.matchで、リクエストされたものがキャッシュに存在するかどうかを確認します。
    caches.match(event.request)
      .then((response) => {
        // もしキャッシュにあれば、そのキャッシュを返します。
        if (response) {
          return response;
        }
        // もしキャッシュになければ、通常通りインターネットから取得しにいきます。
        return fetch(event.request);
      })
  );
});
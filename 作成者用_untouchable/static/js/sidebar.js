document.addEventListener('DOMContentLoaded', () => {
    initializeSidebar();
});

function initializeSidebar() {
    const sidebar = document.getElementById('sidebar');
    const sidebarOverlay = document.getElementById('sidebar-overlay');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebarClose = document.getElementById('sidebar-close');
    
    // サイドバー開閉
    function toggleSidebar() {
        if (sidebar) sidebar.classList.toggle('active');
        if (sidebarOverlay) sidebarOverlay.classList.toggle('active');
    }

    if (sidebarToggle) sidebarToggle.addEventListener('click', toggleSidebar);
    if (sidebarClose) sidebarClose.addEventListener('click', toggleSidebar);
    if (sidebarOverlay) sidebarOverlay.addEventListener('click', toggleSidebar);

    // モード表示の更新
    // APP_MODE変数が定義されているか確認 (メインアプリでは定義されているが、質問アプリではない可能性がある)
    const modeDisplay = document.getElementById('sidebar-mode-display');
    if (modeDisplay) {
        if (typeof APP_MODE !== 'undefined') {
            const modeMap = { 'admin': '管理者', 'scanner': 'スキャン', 'students': '生徒用' };
            modeDisplay.textContent = modeMap[APP_MODE] || APP_MODE;
        } else {
            // APP_MODEがない場合（質問アプリなど）
            modeDisplay.textContent = '質問受付';
        }
    }

    // ネットワークステータスの監視
    updateSidebarNetworkStatus();
    window.addEventListener('online', updateSidebarNetworkStatus);
    window.addEventListener('offline', updateSidebarNetworkStatus);

    // サーバー通信チェック（定期実行）
    checkSidebarServerHealth();
    setInterval(checkSidebarServerHealth, 30000); // 30秒ごとにチェック

    // 設定モーダルの初期化
    initializeSettingsModal(sidebar, sidebarOverlay);
}

function updateSidebarNetworkStatus() {
    const statusEl = document.getElementById('sidebar-network-status');
    const textEl = document.getElementById('network-text');
    if (!statusEl || !textEl) return;

    const isOnline = navigator.onLine;
    const dot = statusEl.querySelector('.status-dot');

    if (isOnline) {
        dot.className = 'status-dot green';
        textEl.textContent = 'オンライン';
    } else {
        dot.className = 'status-dot red';
        textEl.textContent = 'オフライン';
    }
}

async function checkSidebarServerHealth() {
    const statusEl = document.getElementById('sidebar-server-status');
    if (!statusEl) return;

    if (!navigator.onLine) {
        statusEl.textContent = '通信不可';
        statusEl.style.color = '#d9534f'; // danger color
        return;
    }

    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 3000);
        
        // 設定APIへのパスは、メインアプリ・サブアプリ問わず /api/settings でアクセス可能と仮定
        const res = await fetch('/api/settings', { method: 'GET', signal: controller.signal });
        clearTimeout(timeoutId);

        if (res.ok) {
            statusEl.textContent = '正常';
            statusEl.style.color = '#28a745';
        } else {
            throw new Error('Status not OK');
        }
    } catch (e) {
        statusEl.textContent = 'エラー';
        statusEl.style.color = '#d9534f';
    }
}

function initializeSettingsModal(sidebar, sidebarOverlay) {
    const openBtn = document.getElementById('open-settings-btn');
    const closeBtn = document.getElementById('close-settings-btn');
    const modal = document.getElementById('settings-modal');
    const form = document.getElementById('settings-form');

    if (!openBtn || !modal) return;

    openBtn.addEventListener('click', async () => {
        // 設定値のロード
        try {
            const res = await fetch('/api/settings');
            if (res.ok) {
                const data = await res.json();
                if (form) {
                    Object.keys(data).forEach(key => {
                        if (form.elements[key]) {
                            form.elements[key].value = data[key];
                        }
                    });
                }
            }
        } catch (e) {
            console.error("設定取得エラー:", e);
            alert("設定の読み込みに失敗しました");
        }

        modal.style.display = 'flex';
        // サイドバーを閉じる
        if (sidebar) sidebar.classList.remove('active');
        if (sidebarOverlay) sidebarOverlay.classList.remove('active');
    });

    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            modal.style.display = 'none';
        });
    }

    if (form) {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            if (!confirm("設定を保存しますか？\n変更内容によっては再起動が必要です。")) return;

            const formData = new FormData(form);
            const data = Object.fromEntries(formData.entries());

            try {
                const res = await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });
                const result = await res.json();
                
                alert(result.message);
                if (res.ok) {
                    modal.style.display = 'none';
                    if (confirm("設定を反映するためページを再読み込みしますか？")) {
                        location.reload();
                    }
                }
            } catch (e) {
                console.error("設定保存エラー:", e);
                alert("保存中にエラーが発生しました");
            }
        });
    }
}
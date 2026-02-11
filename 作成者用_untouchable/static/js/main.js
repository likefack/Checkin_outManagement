// --- DOMContentLoaded ---
document.addEventListener('DOMContentLoaded', () => {
    initializePage();
});

// --- グローバル変数・定数 ---
let studentsData = {};
let currentAttendees = [];
let isCalendarOpen = false;
let originalThemeColor = '#4a90e2';
let lastScannedId = null; 
const exitTimers = {}; // { log_id: timerId }
let isNavigating = false; // 画面遷移中フラグ
let globalEventSource = null; // SSE接続管理用

// ページ離脱時にリソースを解放する
window.addEventListener('beforeunload', () => {
    if (globalEventSource) {
        globalEventSource.close();
        globalEventSource = null;
    }
}); 
// 通信タイムアウト設定 (ms)
const FETCH_TIMEOUT_MS = 3000; // 3秒で諦めて保存
const SLOW_REQUEST_NOTIFY_MS = 1500; // 1.5秒経過したら「通信中」と表示

// オフライン送信待ちキュー（ローカルストレージから読み込み）
let offlineQueue = JSON.parse(localStorage.getItem('offlineQueue')) || [];
// 同期処理中かどうかのフラグ
let isSyncing = false;

// --- DOM要素の取得 ---
const dom = {
    currentTime: document.getElementById('current-time'),
    appNameHeader: document.querySelector('.header-main h1'),
    gradeSelect: document.getElementById('grade-select'),
    classSelect: document.getElementById('class-select'),
    numberSelect: document.getElementById('number-select'),
    seatSelect: document.getElementById('seat-select'),
    seatSelectorItem: document.getElementById('seat-selector-item'),
    studentNameContainer: document.getElementById('student-name-container'),
    studentNameDisplay: document.getElementById('student-name-display'),
    actionButtonContainer: document.getElementById('action-button-container'),
    attendanceTableBody: document.getElementById('attendance-table-body'),
    qrInput: document.getElementById('qr-input'),
    exitAllBtn: document.getElementById('exit-all-btn'),
    createReportBtn: document.getElementById('create-report-btn'),
    reportPeriodInput: document.getElementById('report-period'),
    toastContainer: document.getElementById('toast-container'),
    openCameraBtn: document.getElementById('open-camera-btn'),
    cameraModal: document.getElementById('camera-modal'),
    cameraVideo: document.getElementById('camera-video'),
    cameraCanvas: document.getElementById('camera-canvas'),
    closeCameraBtn: document.getElementById('close-camera-btn'),
    // サイドバー関連
    sidebarToggle: document.getElementById('sidebar-toggle'),
    sidebarClose: document.getElementById('sidebar-close'),
    sidebar: document.getElementById('sidebar'),
    sidebarOverlay: document.getElementById('sidebar-overlay'),
    // 設定・ステータス関連
    sidebarModeDisplay: document.getElementById('sidebar-mode-display'),
    sidebarNetworkStatus: document.getElementById('sidebar-network-status'),
    networkText: document.getElementById('network-text'),
    sidebarServerStatus: document.getElementById('sidebar-server-status'),
    openSettingsBtn: document.getElementById('open-settings-btn'),
    settingsModal: document.getElementById('settings-modal'),
    settingsForm: document.getElementById('settings-form'),
    closeSettingsBtn: document.getElementById('close-settings-btn'),
    themeColorInput: document.getElementById('settings-theme-color'),
    resetThemeColorBtn: document.getElementById('reset-theme-color-btn')
};

/**
 * @function initializePage
 * @description ページの初期化を行うメイン関数
 */
function initializePage() {
    setupSSE();
    updateTime();
    setInterval(updateTime, 1000);
    
    resetSelect(dom.gradeSelect, "");
    resetSelect(dom.classSelect, "");
    resetSelect(dom.numberSelect, "");
    resetSelect(dom.seatSelect, "");

    fetchInitialData();
    setupEventListeners();
    setupSidebarLogic(); // 追加: サイドバー機能の初期化
    
    // オンライン復帰時にキューを処理するイベントリスナー
    window.addEventListener('online', () => {
        processOfflineQueue();
        updateNetworkStatusUI(); // 追加: UI更新
    });
    window.addEventListener('offline', updateNetworkStatusUI); // 追加: オフライン時UI更新
    // ページ読み込み時に未送信があれば処理を試みる
    if (navigator.onLine && offlineQueue.length > 0) {
        processOfflineQueue();
    }

    // 【追加】サーバー復帰監視：キューがある場合、5秒ごとに送信を試みる
    setInterval(() => {
        if (navigator.onLine && offlineQueue.length > 0 && !isSyncing) {
            console.log("サーバー復帰確認のため、キューの同期を試みます...");
            processOfflineQueue();
        }
    }, 1000);

    if (APP_MODE === 'admin') {
        flatpickr(dom.reportPeriodInput, {
            mode: "range",
            dateFormat: "Y-m-d",
            locale: { ...flatpickr.l10ns.ja, rangeSeparator: ' ~ ' },
            defaultDate: [new Date(), new Date()],
            onOpen: () => { isCalendarOpen = true; },
            onClose: () => { isCalendarOpen = false; }
        });
        focusQrInput();
    }

    // 記録編集ボタン（管理者モード用）の制御
    const goEditBtn = document.getElementById('go-edit-btn');
    if (goEditBtn) {
        goEditBtn.addEventListener('click', (e) => {
            if (isNavigating) {
                e.preventDefault();
                return;
            }
            isNavigating = true;
            goEditBtn.disabled = true;
            goEditBtn.textContent = '移動中...';
            window.location.href = goEditBtn.dataset.href;
        });
    }
    
    // スキャナモードの「記録編集」ボタンなどのインラインonclick対策
    // DOM内のonclick属性を持つボタンを全てチェックし、遷移系であれば保護する
    const buttons = document.querySelectorAll('button[onclick*="location.href"]');
    buttons.forEach(btn => {
        const originalOnClick = btn.getAttribute('onclick');
        // location.href='...' の部分を抽出
        const match = originalOnClick.match(/location\.href=['"]([^'"]+)['"]/);
        if (match) {
            const url = match[1];
            btn.removeAttribute('onclick'); // 元のハンドラを削除
            btn.addEventListener('click', (e) => {
                if (isNavigating) {
                    e.preventDefault();
                    return;
                }
                isNavigating = true;
                btn.disabled = true;
                window.location.href = url;
            });
        }
    });
}

/**
 * @function updateTime
 * @description ヘッダーの現在時刻を更新する
 */
function updateTime() {
    const now = new Date();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    const week = ['日', '月', '火', '水', '木', '金', '土'][now.getDay()];
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    const seconds = String(now.getSeconds()).padStart(2, '0');
    dom.currentTime.textContent = `${month}/${day}(${week}) ${hours}:${minutes}:${seconds}`;
}

/**
 * @function fetchInitialData
 * @description サーバーのAPIを叩いて、初期データを取得する
 */
// 修正: キャッシュ回避のためにタイムスタンプ(?t=...)を付与
async function fetchInitialData() {
    try {
        const response = await fetch(`/api/initial_data?t=${new Date().getTime()}`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const data = await response.json();
        
        studentsData = data.students;
        currentAttendees = data.attendees;
        
        // 【追加】取得成功時にローカルストレージに最新のマスタデータを保存
        localStorage.setItem('cachedStudentsData', JSON.stringify(studentsData));
        
        // オフラインキューにある変更を適用して、UIの状態を最新にする
        applyOfflineChanges();

        populateGradeSelect();
        renderAttendanceTable();
    } catch (error) {
        console.error('初期データの読み込みに失敗しました:', error);
        
        // 【追加】通信エラー時はキャッシュからの読み込みを試みる
        const cached = localStorage.getItem('cachedStudentsData');
        if (cached) {
            studentsData = JSON.parse(cached);
            // currentAttendees（現在の入室者リスト）はリアルタイム性が重要なので
            // オフライン時は空にするか、あるいは別途キャッシュするか判断が分かれますが、
            // ここでは「名簿（studentsData）」の復旧を優先し、入室者リストは空（または以前の状態）とします。
            // ※もし入室者リストもキャッシュしたい場合は同様にlocalStorageへ保存してください。
            
            // オフラインキューにある変更を適用して、UIの状態を最新にする
            applyOfflineChanges();

            populateGradeSelect();
            showToast("オフラインモード: 保存された名簿データを使用しています。");
        } else {
            showToast("エラー: サーバーに接続できず、保存されたデータもありません。");
        }
    }
}

/**
 * @function setupEventListeners
 * @description ページ内の要素にイベントリスナーを設定する
 */
function setupEventListeners() {
    dom.appNameHeader.addEventListener('click', () => {
        location.reload();
    });
    dom.gradeSelect.addEventListener('change', onGradeChange);
    dom.classSelect.addEventListener('change', onClassChange);
    dom.numberSelect.addEventListener('change', onNumberChange);
    dom.seatSelect.addEventListener('change', onSeatChange);
    dom.attendanceTableBody.addEventListener('click', handleTableClick);

    if (dom.exitAllBtn) {
        dom.exitAllBtn.addEventListener('click', handleExitAll);
    }

    if (APP_MODE === 'admin') {
        //意図的に他の入力要素へフォーカスした場合は、QR入力欄への強制フォーカス戻しを行わない
        dom.qrInput.addEventListener('blur', (e) => {
            const newTarget = e.relatedTarget;
            // フォーカス移動先がセレクトボックス、入力、ボタン、またはカレンダー等の場合は何もしない
            if (newTarget && (
                newTarget.tagName === 'SELECT' || 
                newTarget.tagName === 'INPUT' || 
                newTarget.tagName === 'BUTTON' ||
                newTarget.closest('.flatpickr-calendar')
            )) {
                return;
            }
            focusQrInput();
        });
        // --- 修正: IME確定(compositionend)とEnterキー(keydown)の両方で入力を検知 ---
        const processInput = (e) => {
            // IME入力中のEnterキーイベントは無視（compositionendで処理するため）
            if (e.type === 'keydown' && e.isComposing) return;
            
            // IME確定時、またはEnterキーが押された時に処理を実行
            if (e.type === 'compositionend' || (e.type === 'keydown' && e.key === 'Enter')) {
                // keydownのEnterならデフォルトのフォーム送信動作等を防ぐ
                if (e.type === 'keydown') e.preventDefault();
                handleQrInput(e);
            }
        };

        dom.qrInput.addEventListener('keydown', processInput); 
        dom.qrInput.addEventListener('compositionend', processInput);

        dom.createReportBtn.addEventListener('click', handleCreateReport);
    }

    if (dom.openCameraBtn) {
        dom.openCameraBtn.addEventListener('click', openCamera);
    }
    if (dom.closeCameraBtn) {
        dom.closeCameraBtn.addEventListener('click', closeCamera);
    }

    // サイドバー操作
    if (dom.sidebarToggle) {
        dom.sidebarToggle.addEventListener('click', toggleSidebar);
    }
    if (dom.sidebarClose) {
        dom.sidebarClose.addEventListener('click', toggleSidebar);
    }
    if (dom.sidebarOverlay) {
        dom.sidebarOverlay.addEventListener('click', toggleSidebar);
    }
}

function toggleSidebar() {
    dom.sidebar.classList.toggle('active');
    dom.sidebarOverlay.classList.toggle('active');
}

/**
 * サイドバーのステータス表示と設定モーダルのロジック
 */
function setupSidebarLogic() {
    // サイドバー内のリンクに対する連続クリック防止
    const sidebarLinks = document.querySelectorAll('.sidebar-nav a');
    sidebarLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            // ページ内リンク(#)やJavaScriptリンク以外（＝画面遷移するもの）を対象
            const href = link.getAttribute('href');
            if (href && href !== '#' && !href.startsWith('javascript')) {
                if (isNavigating) {
                    e.preventDefault();
                    return;
                }
                // 現在のページと同じリンクでなければフラグを立てる
                if (href !== window.location.pathname + window.location.search) {
                    isNavigating = true;
                    // 視覚的フィードバック（任意）
                    link.style.opacity = '0.5';
                    link.style.cursor = 'wait';
                }
            }
        });
    });

    // 1. モード表示の更新
    if (dom.sidebarModeDisplay) {
        const modeMap = { 'admin': '管理者', 'scanner': 'スキャン', 'students': '生徒用' };
        dom.sidebarModeDisplay.textContent = modeMap[APP_MODE] || APP_MODE;
    }

    // 2. ネットワークステータスの初期表示
    updateNetworkStatusUI();

    // 3. サーバー通信チェック（定期実行）
    setInterval(checkServerHealth, 30000); // 30秒ごとにチェック
    checkServerHealth(); // 初回実行

    // 4. 設定モーダルのイベントリスナー
    if (dom.openSettingsBtn) {
        dom.openSettingsBtn.addEventListener('click', openSettingsModal);
    }
    if (dom.closeSettingsBtn) {
        dom.closeSettingsBtn.addEventListener('click', closeSettingsModal);
    }
    if (dom.settingsForm) {
        dom.settingsForm.addEventListener('submit', handleSettingsSave);
    }

    // テーマカラーのリアルタイムプレビュー
    if (dom.themeColorInput) {
        dom.themeColorInput.addEventListener('input', (e) => {
            document.documentElement.style.setProperty('--primary-color', e.target.value);
        });
    }

    // デフォルトリセット機能
    if (dom.resetThemeColorBtn) {
        dom.resetThemeColorBtn.addEventListener('click', () => {
            const defaultColor = '#4a90e2';
            dom.themeColorInput.value = defaultColor;
            document.documentElement.style.setProperty('--primary-color', defaultColor);
        });
    }
}

function updateNetworkStatusUI() {
    if (!dom.sidebarNetworkStatus) return;
    
    const isOnline = navigator.onLine;
    const dot = dom.sidebarNetworkStatus.querySelector('.status-dot');
    const text = dom.networkText;

    if (isOnline) {
        dot.className = 'status-dot green';
        text.textContent = 'オンライン';
    } else {
        dot.className = 'status-dot red';
        text.textContent = 'オフライン';
    }
}

async function checkServerHealth() {
    if (!dom.sidebarServerStatus) return;
    if (!navigator.onLine) {
        dom.sidebarServerStatus.textContent = '通信不可';
        dom.sidebarServerStatus.style.color = 'var(--danger-color)';
        return;
    }

    try {
        // 軽いエンドポイントを叩いて確認（ここでは設定取得APIを流用）
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 3000);
        
        const res = await fetch('/api/settings', { method: 'GET', signal: controller.signal });
        clearTimeout(timeoutId);

        if (res.ok) {
            dom.sidebarServerStatus.textContent = '正常';
            dom.sidebarServerStatus.style.color = '#28a745';
        } else {
            throw new Error('Status not OK');
        }
    } catch (e) {
        dom.sidebarServerStatus.textContent = 'エラー';
        dom.sidebarServerStatus.style.color = 'var(--danger-color)';
    }
}

async function openSettingsModal() {
    // プレビューのキャンセル用に現在の色を保存
    originalThemeColor = getComputedStyle(document.documentElement).getPropertyValue('--primary-color').trim();

    // 現在の設定を取得してフォームに埋め込む
    try {
        const res = await fetch('/api/settings');
        if (res.ok) {
            const data = await res.json();
            const form = dom.settingsForm;
            if (form) {
                // 各入力欄に値をセット
                Object.keys(data).forEach(key => {
                    if (form.elements[key]) {
                        form.elements[key].value = data[key];
                    }
                });
                // 取得した色をプレビューに反映
                if (data.THEME_COLOR) {
                    document.documentElement.style.setProperty('--primary-color', data.THEME_COLOR);
                }
            }
        }
    } catch (e) {
        console.error("設定取得エラー:", e);
        showToast("設定の読み込みに失敗しました");
    }
    
    dom.settingsModal.style.display = 'flex';
    // サイドバーを閉じる
    dom.sidebar.classList.remove('active');
    dom.sidebarOverlay.classList.remove('active');
}

function closeSettingsModal() {
    // 保存せずに閉じる場合はプレビューを破棄して元の色に戻す
    document.documentElement.style.setProperty('--primary-color', originalThemeColor);
    dom.settingsModal.style.display = 'none';
}

async function handleSettingsSave(e) {
    e.preventDefault();
    if (!confirm("設定を保存しますか？\n変更内容によっては再起動が必要です。")) return;

    const formData = new FormData(dom.settingsForm);
    const data = Object.fromEntries(formData.entries());

    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const result = await res.json();
        
        showToast(result.message);
        if (res.ok) {
            closeSettingsModal();
            // アプリ名などが変わった可能性があるためリロードを推奨
            if (confirm("設定を反映するためページを再読み込みしますか？")) {
                location.reload();
            }
        }
    } catch (e) {
        console.error("設定保存エラー:", e);
        showToast("保存中にエラーが発生しました");
    }
}

// --- 入退室処理 ---

/**
 * @function processApiResponse
 * @description APIからの応答を処理し、通知を表示し、画面を更新する
 */
async function processApiResponse(response) {
    const result = await response.json();

    //APIレスポンスに含まれる`rank`情報を`showToast`関数に渡す
    showToast(result.message, result.rank);
    
    if (response.ok) {
        if (result.achievement && result.achievement.student_message) {
            setTimeout(() => {
                showToast(result.achievement.student_message, result.rank);
            }, 750);
        }

        // 【修正】サーバーからの差分データ(log_data)がある場合、ローカルの配列を更新して即座に反映させる
        if (result.log_data) {
            const newLog = result.log_data;
            // 既存のリストに同じログIDがあるか探す（更新の場合）
            const index = currentAttendees.findIndex(a => a.log_id === newLog.log_id);
            
            if (index !== -1) {
                // 退室処理などの場合：既存データを上書き
                currentAttendees[index] = newLog;
            } else {
                // 新規入室の場合：配列に追加
                currentAttendees.push(newLog);
            }
            
            // 入室時間順などでソートが必要ならここで行う（現在はAPI側でORDER BYしているが、pushしただけだと末尾に追加される）
            // 簡易的にID順あるいは入室時間順にソートしなおす
            currentAttendees.sort((a, b) => {
                // 入室時間の昇順（古い順）
                return new Date(a.entry_time) - new Date(b.entry_time);
            });

            // 【追加】入力フォーム側の判定に使われる生徒データ(studentsData)のステータスも更新する
            // これにより、次回の選択時に「入室/退室」ボタンが正しく判定される
            const sGrade = newLog.grade;
            const sClass = newLog.class;
            const sNumber = newLog.student_number;
            
            if (studentsData[sGrade] && studentsData[sGrade][sClass] && studentsData[sGrade][sClass][sNumber]) {
                const targetStudent = studentsData[sGrade][sClass][sNumber];
                if (newLog.exit_time) {
                    // 退室済みになった場合
                    targetStudent.is_present = false;
                    targetStudent.current_log_id = null;
                } else {
                    // 入室中になった場合
                    targetStudent.is_present = true;
                    targetStudent.current_log_id = newLog.log_id;
                }
            }

            // テーブル再描画（fetchInitialDataを待たずに実行）
            renderAttendanceTable();
        } else {
            // 万が一データがない場合は従来の全取得を行う
            await fetchInitialData();
        }
        // resetAllSelectorsは削除済み
    }
}


async function handleManualEntry() {
    const student = getSelectedStudent();
    const seatNumber = dom.seatSelect.value;
    if (!student || !seatNumber) return showToast("生徒と座席を選択してください。");

    // 【修正】API通信を待たずに即座にUIをリセットし、次の入力を可能にする
    resetAllSelectors();

    const payload = { system_id: student.system_id, seat_number: seatNumber };

    // オフライン判定
    if (!navigator.onLine) {
        saveToOfflineQueue('check_in', payload, `${student.name}さんの入室を受け付けました (オフライン)`);
        // 即座にUI上のステータスを更新する
        student.is_present = true;
        return; // API通信は行わない
    }

    // タイムアウト用コントローラーと通知用タイマーのセット
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    const slowNotifyId = setTimeout(() => showToast("サーバーと通信中...しばらくお待ちください", null), SLOW_REQUEST_NOTIFY_MS);

    try {
        const response = await fetch('/api/check_in', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: controller.signal // タイムアウト設定
        });
        
        // 成功したらタイマー解除
        clearTimeout(timeoutId);
        clearTimeout(slowNotifyId);

        // サーバーが500系エラーなどを返した場合も例外を投げてcatchブロックへ誘導する
        if (!response.ok && response.status >= 500) {
            throw new Error(`Server Error: ${response.status}`);
        }
        await processApiResponse(response);
    } catch (error) {
        clearTimeout(timeoutId);
        clearTimeout(slowNotifyId);
        console.error('入室処理エラー(通信不可):', error);
        
        const isTimeout = error.name === 'AbortError';
        const errorMsg = isTimeout ? 'タイムアウト' : '通信エラー';

        // 通信エラーまたはサーバーエラー時は、オフラインキューに保存して後で再送する
        saveToOfflineQueue('check_in', payload, `${student.name}さんの入室を保存しました (${errorMsg})`);
        
        // 【重要】サーバーダウン時もローカルの状態を進める
        student.is_present = true;
    }
}

async function handleManualExit() {
    const student = getSelectedStudent();
    if (!student) return showToast("生徒を選択してください。");

    // 【修正】API通信を待たずに即座にUIをリセット
    resetAllSelectors();

    const exitTime = new Date().toISOString();
    
    // finalizeExit内でオフライン判定を行うため、そのまま呼び出す
    finalizeExit(student.current_log_id, student.system_id, exitTime);
}

async function handleQrInput(event) {
    const rawId = event.target.value;
    if (!rawId) return;
    event.target.value = ''; 
    await processQrId(rawId);
}

/**
 * QRコードの文字列（ID）を受け取り、入退室処理を行う共通ロジック
 */
async function processQrId(rawId) {
    const normalizedId = normalizeSystemId(rawId);
    
    if (!/^\d{7}$/.test(normalizedId)) {
        // UX向上のため、フォーマット無効（ノイズや無関係なQR）の場合はエラー表示せず無視する
        console.warn(`無効なQRデータ: ${rawId} -> 解析結果: ${normalizedId || '解析不能'}`);
        return;
    }

    resetAllSelectors();

    if (normalizedId === lastScannedId) return; 
    lastScannedId = normalizedId;
    setTimeout(() => { lastScannedId = null; }, 5000);

    const payload = { system_id: normalizedId };

    if (!navigator.onLine) {
        payload.timestamp = new Date().toISOString();
        const studentName = findStudentNameBySystemId(normalizedId) || `ID:${normalizedId}`;
        saveToOfflineQueue('qr_process', payload, `${studentName}さんの入退室を受け付けました (オフライン)`);
        
        const sObj = findStudentObjectBySystemId(normalizedId);
        if (sObj) {
            if (sObj.is_present) {
                sObj.is_present = false;
                sObj.current_log_id = null;
            } else {
                sObj.is_present = true;
            }
        }
        return;
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    const slowNotifyId = setTimeout(() => showToast("サーバーと通信中...しばらくお待ちください", null), SLOW_REQUEST_NOTIFY_MS);

    try {
        const response = await fetch('/api/qr_process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: controller.signal
        });

        clearTimeout(timeoutId);
        clearTimeout(slowNotifyId);

        if (!response.ok && response.status >= 500) {
            throw new Error(`Server Error: ${response.status}`);
        }
        await processApiResponse(response);
    } catch (error) {
        clearTimeout(timeoutId);
        clearTimeout(slowNotifyId);
        console.error('QR処理エラー(通信不可):', error);

        const isTimeout = error.name === 'AbortError';
        const errorMsg = isTimeout ? 'タイムアウト' : '通信エラー';

        const studentName = findStudentNameBySystemId(normalizedId) || `ID:${normalizedId}`;
        saveToOfflineQueue('qr_process', payload, `${studentName}さんの入退室を保存しました (${errorMsg})`);

        // 【重要】サーバーダウン時もローカルの状態を反転させる
        const sObj = findStudentObjectBySystemId(normalizedId);
        if (sObj) {
            if (sObj.is_present) {
                sObj.is_present = false;
                sObj.current_log_id = null;
            } else {
                sObj.is_present = true;
            }
        }
    }
}

function handleTableClick(event) {
    const target = event.target;
    if (target.classList.contains('exit-list-btn')) {
        initiateExitProcess(target);
    } else if (target.classList.contains('undo-btn')) {
        cancelExitProcess(target);
    }
}

function initiateExitProcess(button) {
    const logId = button.dataset.logId;
    const systemId = button.dataset.systemId;
    const pressedAt = new Date().toISOString();
    const row = button.closest('tr');
    const durationCell = row.querySelector('td[data-entry-time]');
    durationCell.dataset.exitTime = pressedAt; 
    
    // カウントダウン初期化
    let timeLeft = 5;
    button.textContent = `取消 (${timeLeft}s)`;
    button.classList.remove('exit-list-btn');
    button.classList.add('undo-btn');

    // 1秒ごとにカウントダウン、0になったら退室確定
    const timerId = setInterval(() => {
        timeLeft--;
        if (timeLeft > 0) {
            button.textContent = `取消 (${timeLeft}s)`;
        } else {
            clearInterval(exitTimers[logId]);
            delete exitTimers[logId];
            finalizeExit(logId, systemId, pressedAt);
        }
    }, 1000);
    
    exitTimers[logId] = timerId;
}

function cancelExitProcess(button) {
    const logId = button.dataset.logId;
    if (exitTimers[logId]) {
        clearInterval(exitTimers[logId]); 
        delete exitTimers[logId];
        const row = button.closest('tr');
        const durationCell = row.querySelector('td[data-entry-time]');
        delete durationCell.dataset.exitTime;
        button.textContent = '退室';
        button.classList.remove('undo-btn');
        button.classList.add('exit-list-btn');
    }
}

async function finalizeExit(logId, systemId, exitTime) {
    const payload = { log_id: logId, system_id: systemId, exit_time: exitTime };

    // オフライン判定
    if (!navigator.onLine) {
        // 退室時はstudentオブジェクトが直接参照できないため、IDから名前を探すヘルパーを利用
        const studentName = findStudentNameBySystemId(systemId) || "退室";
        saveToOfflineQueue('check_out', payload, `${studentName}さんの退室を受け付けました (オフライン)`);

        // 即座にUI上のステータスを更新する
        const sObj = findStudentObjectBySystemId(systemId);
        if (sObj) {
            sObj.is_present = false;
            sObj.current_log_id = null;
        }

        // 【修正】オフライン時はボタンの表示を「同期待ち」に変更し、操作を無効化する
        // これにより「取消 (1s)」などの表示で止まってしまうのを防ぐ
        const row = dom.attendanceTableBody.querySelector(`tr[data-log-id="${logId}"]`);
        if (row) {
            const btn = row.querySelector('button');
            if (btn) {
                btn.textContent = '同期待ち';
                btn.disabled = true;
                btn.classList.remove('undo-btn'); // 黄色のスタイルを解除
                btn.style.opacity = '0.7';        // 無効化を視覚的に表現
            }
        }
        return;
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    const slowNotifyId = setTimeout(() => showToast("サーバーと通信中...しばらくお待ちください", null), SLOW_REQUEST_NOTIFY_MS);

    try {
        const response = await fetch('/api/check_out', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        clearTimeout(slowNotifyId);

        if (!response.ok && response.status >= 500) {
            throw new Error(`Server Error: ${response.status}`);
        }
        await processApiResponse(response);
    } catch (error) {
        clearTimeout(timeoutId);
        clearTimeout(slowNotifyId);
        console.error('退室処理エラー(通信不可):', error);
        
        const isTimeout = error.name === 'AbortError';
        const errorMsg = isTimeout ? 'タイムアウト' : '通信エラー';

        // 名前解決を試みる
        const studentName = findStudentNameBySystemId(systemId) || "退室";
        saveToOfflineQueue('check_out', payload, `${studentName}さんの退室を保存しました (${errorMsg})`);

        // 【重要】サーバーダウン時もローカルの状態を更新する
        const sObj = findStudentObjectBySystemId(systemId);
        if (sObj) {
            sObj.is_present = false;
            sObj.current_log_id = null;
        }

        // オフライン時と同様にボタンの表示を「同期待ち」に変更し、操作を無効化する
        const row = dom.attendanceTableBody.querySelector(`tr[data-log-id="${logId}"]`);
        if (row) {
            const btn = row.querySelector('button');
            if (btn) {
                btn.textContent = '同期待ち';
                btn.disabled = true;
                btn.classList.remove('undo-btn');
                btn.style.opacity = '0.7';
            }
        }
    }
}

async function handleExitAll() {
    if (confirm("本当に全員を退室させますか？\nこの操作は取り消せません。")) {
        try {
            const response = await fetch('/api/exit_all', { method: 'POST' });
            const result = await response.json();
            showToast(result.message);
            if (response.ok) await fetchInitialData();
        } catch (error) {
            console.error('一斉退室処理エラー:', error);
            showToast("エラー: 一斉退室処理中に問題が発生しました。");
        }
    }
}

async function handleCreateReport() {
    const dateRange = dom.reportPeriodInput.value;
    if (!dateRange) { return showToast("エラー: 期間を選択してください。"); }
    let startDate, endDate;
    if (dateRange.includes('~')) { [startDate, endDate] = dateRange.split(' ~ '); } 
    else { startDate = endDate = dateRange; }

    const confirmationMessage = `期間: ${startDate} ~ ${endDate}\n` +
        "この期間で集計レポートを作成します。\n同名のファイルは上書きされます。\n" +
        "（もしExcelでファイルを開いている場合は、閉じてから実行してください）";

    if (confirm(confirmationMessage)) {
        try {
            const response = await fetch('/api/create_report', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ start_date: startDate, end_date: endDate })
            });
            const result = await response.json();
            showToast(result.message);
        } catch (error) {
            console.error('レポート作成エラー:', error);
            showToast("エラー: レポート作成中に問題が発生しました。");
        }
    }
}


function normalizeSystemId(id) {
    if (!id) return '';
    
    // 1. 全角英数字を半角に変換 (IME入力対策)
    let processedId = id.replace(/[Ａ-Ｚａ-ｚ０-９]/g, function(s) {
        return String.fromCharCode(s.charCodeAt(0) - 0xFEE0);
    });

    processedId = processedId.toUpperCase();

    // 2. ID抽出 (誤入力対策)
    // "ID_"があってもなくても、7桁の英数字(0-9, A-F)のパターンを探して抽出する
    // これにより "tyID_20F0946" のような入力から "20F0946" を取り出す
    const match = processedId.match(/(?:ID_)?([0-9A-F]{7})/);
    if (match) {
        processedId = match[1]; // 抽出された7桁部分
    } else {
        // マッチしない場合は空文字を返し、呼び出し元のフォーマットチェックでエラーにする
        return ''; 
    }

    // 3. A-F を 1-6 に変換
    const gradeCharMap = { 'A': '1', 'B': '2', 'C': '3', 'D': '4', 'E': '5', 'F': '6' };
    let normalized = '';
    for (const char of processedId) {
        normalized += gradeCharMap[char] || char;
    }
    return normalized;
}

// --- UI更新・操作系の関数 ---
function populateGradeSelect() {
    const grades = Object.keys(studentsData);
    grades.sort((a, b) => a - b);

    // 現在のDOMのオプション値を取得（空の選択肢を除く）して比較
    const currentOptions = Array.from(dom.gradeSelect.options)
        .map(opt => opt.value)
        .filter(val => val !== "");

    // 変更がない場合はDOM再構築を行わない（操作中のセレクトボックスが閉じるのを防ぐため）
    if (currentOptions.length === grades.length && 
        currentOptions.every((val, index) => val === grades[index])) {
        return;
    }

    const selectedValue = dom.gradeSelect.value;
    resetSelect(dom.gradeSelect, ""); 
    grades.forEach(grade => {
        const option = document.createElement('option');
        option.value = grade;
        const gradeNames = {1:'中1', 2:'中2', 3:'中3', 4:'高1', 5:'高2', 6:'高3'};
        option.textContent = gradeNames[grade] || grade;
        dom.gradeSelect.appendChild(option);
    });
    dom.gradeSelect.value = selectedValue;
}
function onGradeChange() {
    const selectedGrade = dom.gradeSelect.value;
    resetSelect(dom.classSelect, "");
    resetSelect(dom.numberSelect, "");
    dom.classSelect.disabled = true;
    dom.numberSelect.disabled = true;
    clearStudentInfo();
    if (selectedGrade) {
        const classes = Object.keys(studentsData[selectedGrade]);
        classes.sort((a, b) => a - b);
        populateSelect(dom.classSelect, classes);
        dom.classSelect.disabled = false;
    }
    focusQrInput();
}
function onClassChange() {
    const selectedGrade = dom.gradeSelect.value;
    const selectedClass = dom.classSelect.value;
    resetSelect(dom.numberSelect, "");
    dom.numberSelect.disabled = true;
    clearStudentInfo();
    if (selectedGrade && selectedClass) {
        const numbers = Object.keys(studentsData[selectedGrade][selectedClass]);
        numbers.sort((a, b) => a - b);
        populateSelect(dom.numberSelect, numbers);
        dom.numberSelect.disabled = false;
    }
    focusQrInput();
}
function onNumberChange() {
    clearStudentInfo();
    const student = getSelectedStudent();
    if (student) {
        dom.studentNameContainer.style.display = 'block';
        dom.studentNameDisplay.textContent = student.name;
        if (student.is_present) {
            renderActionButton('exit');
        } else {
            populateSeatSelect();
            dom.seatSelectorItem.style.display = 'block';
        }
    }
    focusQrInput();
}
function onSeatChange() {
    const selectedSeat = dom.seatSelect.value;
    if (selectedSeat) renderActionButton('enter');
    else dom.actionButtonContainer.innerHTML = '';
    focusQrInput();
}
function renderActionButton(type) {
    dom.actionButtonContainer.innerHTML = '';
    const button = document.createElement('button');
    if (type === 'enter') {
        button.textContent = '入室';
        button.className = 'enter-btn';
        button.addEventListener('click', handleManualEntry);
    } else {
        button.textContent = '退室';
        button.className = 'exit-btn';
        button.addEventListener('click', handleManualExit);
    }
    dom.actionButtonContainer.appendChild(button);
}
function renderAttendanceTable() {
    // 表示対象リストのフィルタリング
    // adminモードまたはscannerモードの場合は全リストを表示し、それ以外（studentsモード）は手動入室者のみ表示
    const list = (APP_MODE === 'admin' || APP_MODE === 'scanner') ? currentAttendees : currentAttendees.filter(s => s.seat_number);

    // 1. データがない場合の表示処理
    if (list.length === 0) {
        dom.attendanceTableBody.innerHTML = '';
        const row = dom.attendanceTableBody.insertRow();
        const cell = row.insertCell();
        cell.colSpan = 9;
        cell.textContent = "本日、まだ入室者はいません。";
        cell.style.textAlign = 'center';
        return;
    }

    // 「データなし」メッセージが表示されていたらクリア
    if (dom.attendanceTableBody.rows.length === 1 && dom.attendanceTableBody.rows[0].cells.length === 1) {
        dom.attendanceTableBody.innerHTML = '';
    }

    // 2. 削除されたデータの行をDOMから削除
    // 表示すべきlog_idのセットを作成
    const activeLogIds = new Set(list.map(s => s.log_id));
    // 画面上の全行を確認し、リストにないものを削除
    Array.from(dom.attendanceTableBody.rows).forEach(row => {
        const rowLogId = parseInt(row.dataset.logId);
        if (rowLogId && !activeLogIds.has(rowLogId)) {
            row.remove();
        }
    });

    // 3. リストデータに基づいて行を更新・作成（DOM再利用・差分更新）
    list.forEach((student, index) => {
        // 既存の行を探す
        let row = dom.attendanceTableBody.querySelector(`tr[data-log-id="${student.log_id}"]`);
        
        // 新規行の作成
        if (!row) {
            row = dom.attendanceTableBody.insertRow();
            row.dataset.logId = student.log_id;
            // 9つのセルを作成
            for (let i = 0; i < 9; i++) row.insertCell();
        }

        // --- 行スタイルの更新 ---
        if (student.exit_time) {
            if (!row.classList.contains('exited-row')) row.classList.add('exited-row');
        } else {
            if (row.classList.contains('exited-row')) row.classList.remove('exited-row');
        }

        const cells = row.cells;

        // --- 各セルの内容更新（値が変わった場合のみ更新） ---
        // 1. No
        if (cells[0].textContent != index + 1) cells[0].textContent = index + 1;

        // 2. 学年
        const gradeNames = {1:'中1', 2:'中2', 3:'中3', 4:'高1', 5:'高2', 6:'高3'};
        const gradeText = gradeNames[student.grade] || student.grade;
        if (cells[1].textContent !== gradeText) cells[1].textContent = gradeText;

        // 3. 組
        if (cells[2].textContent != student.class) cells[2].textContent = student.class;
        
        // 4. 番号
        if (cells[3].textContent != student.student_number) cells[3].textContent = student.student_number;
        
        // 5. 座席
        const seatText = student.seat_number || 'QR';
        if (cells[4].textContent !== seatText) cells[4].textContent = seatText;
        
        // 6. 氏名
        if (cells[5].textContent !== student.name) cells[5].textContent = student.name;
        
        // 7. 入室時間
        const entryTimeText = new Date(student.entry_time).toLocaleTimeString('ja-JP');
        if (cells[6].textContent !== entryTimeText) cells[6].textContent = entryTimeText;

        // 8. 滞在時間 (data属性と表示の更新)
        const durationCell = cells[7];
        // data属性の更新
        if (durationCell.dataset.entryTime !== student.entry_time) {
            durationCell.dataset.entryTime = student.entry_time;
        }
        if (student.exit_time) {
            // 退室済みならdata-exit-timeを設定し、時間を固定表示
            if (durationCell.dataset.exitTime !== student.exit_time) {
                durationCell.dataset.exitTime = student.exit_time;
                updateDuration(durationCell, student.exit_time);
            }
        } else {
            // 在室中ならdata-exit-timeを削除
            if (durationCell.dataset.exitTime) delete durationCell.dataset.exitTime;
            // 初回表示時などで空の場合は計算して表示
            if (!durationCell.textContent) updateDuration(durationCell);
        }

        // 9. 退室/アクションセル
        const actionCell = cells[8];
        if (!actionCell.classList.contains('action-cell')) actionCell.classList.add('action-cell');

        if (student.exit_time) {
            // 退室済み：時刻を表示
            const exitTimeText = new Date(student.exit_time).toLocaleTimeString('ja-JP');
            if (actionCell.textContent !== exitTimeText) {
                actionCell.textContent = exitTimeText;
            }
        } else {
            // 在室中
            // 【重要】退室カウントダウン中かどうかを確認
            if (exitTimers[student.log_id]) {
                // カウントダウン中（退室処理中）なら、DOMを書き換えない！
                // これにより「取消 (3s)」などの表示状態とイベントが維持される
            } else {
                // カウントダウン中でない場合
                const btn = actionCell.querySelector('button');
                // ボタンがない、または「取消」ボタンが残ってしまっている（状態不整合）場合は初期化
                if (!btn || btn.classList.contains('undo-btn')) {
                     actionCell.innerHTML = `<button class="exit-list-btn" data-log-id="${student.log_id}" data-system-id="${student.system_id}">退室</button>`;
                } else {
                    // 既に退室ボタンがあるなら、ID属性の念のための更新のみ
                    if (btn.dataset.logId != student.log_id) btn.dataset.logId = student.log_id;
                    if (btn.dataset.systemId != student.system_id) btn.dataset.systemId = student.system_id;
                }
            }
        }
    });

    // リスト描画後にキャッシュを再構築し、タイマーを開始
    startDurationTimers();
}
function populateSelect(selectElement, optionsArray) { 
    optionsArray.forEach(item => {
        const option = document.createElement('option');
        option.value = item;
        option.textContent = item;
        selectElement.appendChild(option);
    });
}
function resetSelect(selectElement, defaultText) {
    selectElement.innerHTML = `<option value="">${defaultText}</option>`;
}
function clearStudentInfo() {
    dom.studentNameDisplay.textContent = '';
    dom.actionButtonContainer.innerHTML = '';
    dom.seatSelectorItem.style.display = 'none'; 
    dom.studentNameContainer.style.display = 'none';
    resetSelect(dom.seatSelect, "");
}
function resetAllSelectors() {
    dom.gradeSelect.value = "";
    resetSelect(dom.classSelect, "");
    dom.classSelect.disabled = true;
    resetSelect(dom.numberSelect, "");
    dom.numberSelect.disabled = true;
    clearStudentInfo();
}
function populateSeatSelect() { 
    resetSelect(dom.seatSelect, "");
    
    // 臨時教室の選択肢を追加
    const extraRooms = ['座席なし', '223教室', '224教室', '225教室'];
    extraRooms.forEach(room => {
        const option = document.createElement('option');
        option.value = room;
        option.textContent = room;
        dom.seatSelect.appendChild(option);
    });

    // 座席番号 (1〜72) を追加
    for (let i = 1; i <= 72; i++) {
        const option = document.createElement('option');
        option.value = i;
        option.textContent = i;
        dom.seatSelect.appendChild(option);
    }
}
function getSelectedStudent() {
    const grade = dom.gradeSelect.value;
    const cls = dom.classSelect.value;
    const num = dom.numberSelect.value;
    if (grade && cls && num) {
        return studentsData[grade]?.[cls]?.[num] || null;
    }
    return null;
}
/**
 * @function showToast
 * @description 画面右上に通知（トースト）を表示する。称号に合わせて色を変える
 */
function showToast(message, rank = null) {
    const toast = document.createElement('div');
    toast.className = 'toast';

    //  称号とCSSクラスをマッピング 
    const rankMap = {
        "首席利用者": "gold",
        "次席利用者": "silver",
        "三席利用者": "bronze"
    };
    const rankClass = rankMap[rank];
    if (rankClass) {
        toast.classList.add(rankClass);
    }
    
    toast.textContent = message;
    dom.toastContainer.appendChild(toast);
    setTimeout(() => { toast.classList.add('show'); }, 10);
    setTimeout(() => {
        toast.classList.remove('show');
        toast.addEventListener('transitionend', () => toast.remove());
    }, 5000);
}
let durationInterval = null;
let activeDurationCells = [];

function startDurationTimers() {
    if (durationInterval) clearInterval(durationInterval);
    
    // レンダリング直後のタイミングで、更新が必要なセル（在室中）をキャッシュする
    activeDurationCells = Array.from(dom.attendanceTableBody.querySelectorAll('td[data-entry-time]'))
                               .filter(cell => !cell.dataset.exitTime);

    durationInterval = setInterval(() => {
        // キャッシュされた要素のみを更新し、毎秒のDOMクエリを回避する
        activeDurationCells.forEach(cell => {
            updateDuration(cell);
        });
    }, 1000);
}
function updateDuration(cell, exitTime = null) {
    const entryTime = new Date(cell.dataset.entryTime);
    const endTime = exitTime ? new Date(exitTime) : new Date();
    if(isNaN(entryTime.getTime())) return;
    const diffSeconds = Math.floor((endTime - entryTime) / 1000);
    const hours = Math.floor(diffSeconds / 3600);
    const minutes = Math.floor((diffSeconds % 3600) / 60);
    const seconds = diffSeconds % 60;
    cell.textContent = `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    if (exitTime) cell.dataset.exitTime = exitTime;
}
function focusQrInput() {
    if (dom.qrInput && !isCalendarOpen) {
        dom.qrInput.focus();
    }
}

/**
 * IDから生徒名を検索するヘルパー関数
 */
function findStudentNameBySystemId(systemId) {
    // studentsDataは {grade: {class: {number: studentObj}}} の構造
    // 全探索してIDが一致する生徒を探す
    for (const grade in studentsData) {
        for (const cls in studentsData[grade]) {
            for (const num in studentsData[grade][cls]) {
                const s = studentsData[grade][cls][num];
                // 型不一致を防ぐため == で比較、または文字列化して比較
                if (String(s.system_id) === String(systemId)) {
                    return s.name;
                }
            }
        }
    }
    return null;
}

/**
 * オフライン時のアクションをキューに保存する
 */
function saveToOfflineQueue(actionType, payload, toastMessage = null) {
    // 現在時刻を記録（サーバー送信時に使用）
    // 手動入退室ではすでにpayloadに時刻が含まれる場合があるが、
    // QR処理などのためにここで一律付与または確認する
    
    // 修正: 条件分岐を見直し、アクションタイプに応じて適切なキーで時刻を保存する
    if (actionType === 'check_in') {
        // 手動入室: entry_timeが必要
        if (!payload.entry_time) {
            payload.entry_time = new Date().toISOString();
        }
    } else if (actionType === 'check_out') {
        // 手動退室: exit_timeが必要
        if (!payload.exit_time) {
            payload.exit_time = new Date().toISOString();
        }
    } else {
        // QR処理など (actionType === 'qr_process'): timestampが必要
        if (!payload.timestamp && !payload.entry_time && !payload.exit_time) {
            payload.timestamp = new Date().toISOString();
        }
    }

    const item = {
        action: actionType,
        payload: payload,
        queuedAt: new Date().toISOString()
    };
    
    offlineQueue.push(item);
    localStorage.setItem('offlineQueue', JSON.stringify(offlineQueue));
    
    // 具体的で安心感のあるメッセージを表示
    const msg = toastMessage || `データを保存しました (オフライン)。復帰時に送信されます。`;
    showToast(msg, null);
}

/**
 * オフラインキューに溜まったデータを順次送信する
 */
async function processOfflineQueue() {
    // 既に実行中、キューが空、またはオフラインの場合は何もしない
    if (isSyncing || offlineQueue.length === 0 || !navigator.onLine) return;

    isSyncing = true; // ロック開始

    try {
        console.log(`オフラインキューの同期を開始します (${offlineQueue.length}件)`);
        
        // 初回のみトーストを出す（定期実行で毎回出るとうるさいため、consoleのみにするか、控えめな表示にする）
        // ここでは通常通り表示しますが、必要に応じて調整してください
        // showToast(`通信復帰: ${offlineQueue.length}件のデータを送信中...`); 

        // 配列のコピーを作成して処理（処理成功したものから削除するため）
        const queueToProcess = [...offlineQueue];
        let successCount = 0;

        for (const item of queueToProcess) {
            let url = '';
            if (item.action === 'check_in') url = '/api/check_in';
            else if (item.action === 'check_out') url = '/api/check_out';
            else if (item.action === 'qr_process') url = '/api/qr_process';

            // タイムアウト設定を追加（サーバーが応答しない場合に長時間ロックするのを防ぐ）
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);

            try {
                const response = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(item.payload),
                    signal: controller.signal // タイムアウト適用
                });
                clearTimeout(timeoutId);

                if (response.ok) {
                    // 送信成功したらキューから削除
                    offlineQueue.shift(); // 先頭（一番古いもの）を削除
                    localStorage.setItem('offlineQueue', JSON.stringify(offlineQueue));
                    successCount++;
                } else if (response.status === 409) {
                    console.warn('同期スキップ: 既にサーバー側で処理済みです (409 Conflict)');
                    offlineQueue.shift();
                    localStorage.setItem('offlineQueue', JSON.stringify(offlineQueue));
                } else {
                    console.error('同期エラー: サーバーがエラーを返しました', response.status);
                    // 500エラーなどの場合、サーバーは生きているが処理に失敗している可能性がある
                    // この場合、キューを詰まらせないために次のアイテムへ進むか、
                    // あるいはサーバー復旧待ちとしてループを抜けるか。
                    // ここでは「サーバーダウン」の可能性が高いため、ループを抜けて次回の定期実行に委ねる
                    break;
                }
            } catch (error) {
                clearTimeout(timeoutId);
                console.error('同期通信エラー:', error);
                // 通信エラー（タイムアウトや切断）なら処理を中断して次回に持ち越し
                break; 
            }
        }

        if (successCount > 0) {
            showToast(`${successCount}件のデータを同期しました。`);
            // リストを最新にする
            fetchInitialData();
        }
    } finally {
        isSyncing = false; // ロック解除（必ず実行）
    }
}

/**
 * @function setupSSE
 * @description サーバーからの更新通知を受け取るためのSSE接続を設定する
 */
function setupSSE() {
    if (globalEventSource) {
        globalEventSource.close();
    }
    globalEventSource = new EventSource('/api/stream');
    
    globalEventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'update') {
            console.log("更新通知を受信しました。リストを更新します。");
            fetchInitialData();
        }
    };

    globalEventSource.onerror = (err) => {
        console.warn("SSE接続エラー (再接続を試みます):", err);
        // EventSourceは自動で再接続するため、ここでは特別な処理は不要
    };
}

/**
 * IDから生徒オブジェクトそのものを検索するヘルパー関数
 */
function findStudentObjectBySystemId(systemId) {
    for (const grade in studentsData) {
        for (const cls in studentsData[grade]) {
            for (const num in studentsData[grade][cls]) {
                const s = studentsData[grade][cls][num];
                if (String(s.system_id) === String(systemId)) {
                    return s;
                }
            }
        }
    }
    return null;
}

/**
 * オフラインキューにある操作を現在のstudentsDataに適用し、
 * 最新のステータス（在室/退室）をシミュレーションする関数
 */
function applyOfflineChanges() {
    if (!offlineQueue || offlineQueue.length === 0) return;
    
    offlineQueue.forEach(item => {
        let sysId = null;
        let type = null; // 'in' or 'out'

        if (item.action === 'check_in') {
            sysId = item.payload.system_id;
            type = 'in';
        } else if (item.action === 'check_out') {
            sysId = item.payload.system_id;
            type = 'out';
        } else if (item.action === 'qr_process') {
            sysId = item.payload.system_id;
            // QRの場合はその時点でのステータスを反転させる必要がある
            const s = findStudentObjectBySystemId(sysId);
            if (s) {
               if (s.is_present) type = 'out';
               else type = 'in';
            }
        }

        if (sysId) {
            const s = findStudentObjectBySystemId(sysId);
            if (s) {
                if (type === 'in') {
                    s.is_present = true;
                } else if (type === 'out') {
                    s.is_present = false;
                    s.current_log_id = null;
                }
            }
        }
    });
}

// --- カメラ/QRスキャン関連ロジック ---

let videoStream = null;
let animationFrameId = null;

async function openCamera() {
    try {
        const constraints = {
            video: { facingMode: "environment" } // 背面カメラを優先
        };
        videoStream = await navigator.mediaDevices.getUserMedia(constraints);
        dom.cameraVideo.srcObject = videoStream;
        dom.cameraVideo.setAttribute("playsinline", true);
        
        // 映像の準備が整ってから表示するように変更
        dom.cameraVideo.onloadeddata = () => {
            dom.cameraModal.style.display = 'flex';
            animationFrameId = requestAnimationFrame(tick);
        };
        
        dom.cameraVideo.play();
    } catch (err) {
        console.error("カメラの起動に失敗しました:", err);
        showToast("カメラを起動できませんでした。ブラウザの権限設定を確認してください。");
    }
}

function closeCamera() {
    if (videoStream) {
        videoStream.getTracks().forEach(track => track.stop());
        videoStream = null;
    }
    if (animationFrameId) {
        cancelAnimationFrame(animationFrameId);
        animationFrameId = null;
    }
    dom.cameraModal.style.display = 'none';
}

/**
 * 毎フレームの解析ループ
 */
function tick() {
    if (dom.cameraVideo.readyState === dom.cameraVideo.HAVE_ENOUGH_DATA) {
        const canvas = dom.cameraCanvas;
        const video = dom.cameraVideo;
        const context = canvas.getContext("2d", { willReadFrequently: true });

        canvas.height = video.videoHeight;
        canvas.width = video.videoWidth;
        context.drawImage(video, 0, 0, canvas.width, canvas.height);

        const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
        const code = jsQR(imageData.data, imageData.width, imageData.height, {
            inversionAttempts: "dontInvert",
        });

        if (code) {
            console.log("QRコードを検出:", code.data);
            // 検出成功時の処理を実行（モーダルは閉じない）
            processQrId(code.data);
            
            // 連続スキャン時に画面が固まったように見えないよう、
            // 検出直後に少し待機してから次のフレームを要求する（約1秒の間隔を空ける）
            setTimeout(() => {
                if (videoStream) {
                    animationFrameId = requestAnimationFrame(tick);
                }
            }, 2000);
            return; 
        }
    }
    animationFrameId = requestAnimationFrame(tick);
}
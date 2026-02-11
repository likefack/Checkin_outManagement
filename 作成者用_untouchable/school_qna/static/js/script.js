// ページが全部読み込まれたら、この中の処理を開始する合図

// --- フォームリセット専門の関数 ---
function handleFormResetLogic() {
    const formElement = document.getElementById('question-form'); 
    if (formElement) { // formElement (質問フォーム) が存在するページでのみ実行
        if (sessionStorage.getItem('formSuccessfullySubmitted') === 'true') {
            formElement.reset(); 
            if (typeof updateSubCategories === "function") { 
                updateSubCategories();
            }
            const previewContainer = document.getElementById('image-preview-container');
            if(previewContainer) previewContainer.innerHTML = '';
            sessionStorage.removeItem('formSuccessfullySubmitted');
        }
    }
}

// 【追加】サイドバー制御
document.addEventListener('DOMContentLoaded', function() {
    const sidebar = document.getElementById('sidebar');
    const sidebarOverlay = document.getElementById('sidebar-overlay');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebarClose = document.getElementById('sidebar-close');

    // ステータス表示要素の取得
    const networkStatus = document.getElementById('sidebar-network-status');
    const networkText = document.getElementById('network-text');
    const serverStatus = document.getElementById('sidebar-server-status');

    function openSidebar() {
        if(sidebar) sidebar.classList.add('active');
        if(sidebarOverlay) sidebarOverlay.classList.add('active');
    }
    function closeSidebar() {
        if(sidebar) sidebar.classList.remove('active');
        if(sidebarOverlay) sidebarOverlay.classList.remove('active');
    }

    // ネットワーク状態（ブラウザの接続性）の更新
    function updateNetworkStatusUI() {
        if (!networkStatus || !networkText) return;
        const isOnline = navigator.onLine;
        const dot = networkStatus.querySelector('.status-dot');
        if (isOnline) {
            if(dot) dot.className = 'status-dot green';
            networkText.textContent = 'オンライン';
        } else {
            if(dot) dot.className = 'status-dot red';
            networkText.textContent = 'オフライン';
        }
    }

    // サーバー通信状態のチェック（メインアプリのロジックを流用）
    async function checkServerHealth() {
        if (!serverStatus) return;
        if (!navigator.onLine) {
            serverStatus.textContent = '通信不可';
            serverStatus.style.color = '#d9534f';
            return;
        }
        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 3000);
            // サーバーの応答を確認
            const res = await fetch('/api/settings', { method: 'GET', signal: controller.signal });
            clearTimeout(timeoutId);
            if (res.ok) {
                serverStatus.textContent = '正常';
                serverStatus.style.color = '#28a745';
            } else {
                throw new Error();
            }
        } catch (e) {
            serverStatus.textContent = 'エラー';
            serverStatus.style.color = '#d9534f';
        }
    }

    if(sidebarToggle) sidebarToggle.addEventListener('click', openSidebar);
    if(sidebarClose) sidebarClose.addEventListener('click', closeSidebar);
    if(sidebarOverlay) sidebarOverlay.addEventListener('click', closeSidebar);

    // ステータス監視の初期化
    updateNetworkStatusUI();
    checkServerHealth();

    // イベントリスナーと定期実行の設定
    window.addEventListener('online', () => {
        updateNetworkStatusUI();
        checkServerHealth();
    });
    window.addEventListener('offline', updateNetworkStatusUI);
    setInterval(checkServerHealth, 30000); // 30秒ごとにチェック
});

// --- 音声再生のグローバル管理 ---
let isAudioUnlocked = false;
// HTMLのbodyタグからプレフィックス(/qna)を取得
const urlPrefix = document.body.getAttribute('data-url-prefix') || '';
const notificationSound = new Audio(`${urlPrefix}/static/sounds/notification.m4a`);

// ユーザーの操作をきっかけに、音声再生のロックを解除する関数
const unlockAudio = () => {
    // すでに許可されていれば、何もしない
    if (isAudioUnlocked) {
        // 念のため、不要になったリスナーを削除
        document.body.removeEventListener('click', unlockAudio);
        document.body.removeEventListener('touchstart', unlockAudio);
        return;
    }
    // 音を鳴らしてすぐに止めることで、ブラウザから再生許可を得る
    notificationSound.play().then(() => {
        notificationSound.pause();
        notificationSound.currentTime = 0;
        isAudioUnlocked = true; // 許可状態を記録
        console.log("ユーザー操作により、音声再生が準備されました。");
        // 一度成功すれば不要なので、リスナーを削除
        document.body.removeEventListener('click', unlockAudio);
        document.body.removeEventListener('touchstart', unlockAudio);
    }).catch(e => {
        // ユーザーがまだ何も操作していない場合などに失敗することがある
        // この場合、リスナーは削除されず、次の操作で再度試行される
        console.warn("音声のロック解除試行に失敗。次の操作で再試行します。");
    });
};
// ★★★ 修正ここまで ★★★


// --- メインの処理 (ページ読み込み完了時に実行) ---
document.addEventListener('DOMContentLoaded', function() {
    console.log("JavaScript が動き始めました！ (DOMContentLoaded)");

    // --- 各機能で使うHTML要素を最初にまとめて取得 ---
    const pendingCountSpan = document.getElementById('pending-count');
    const pendingCountSpanList = document.getElementById('pending-count-list');
    const subjectSelect = document.getElementById('subject');
    const subCategorySelect = document.getElementById('sub_category');
    const formElement = document.getElementById('question-form');
    const clockElement = document.getElementById('realtime-clock');
    const subCategoriesDataElement = document.getElementById('sub-categories-json');
    const bulkDeleteForm = document.getElementById('bulk-delete-form');
    const bulkDeleteButton = document.getElementById('bulk-delete-button');
    const selectAllCheckbox = document.getElementById('select-all-checkbox');
    const photoInput = document.getElementById('photo-input');
    const imagePreviewContainer = document.getElementById('image-preview-container');
    
    handleFormResetLogic();

    // --- URLから席番号を取得して自動入力・ロックする機能 ---
    const urlParams = new URLSearchParams(window.location.search);
    const seatFromUrl = urlParams.get('seat');
    const seatSelect = document.getElementById('seat_num');

    if (seatFromUrl && seatSelect) {
        const optionExists = Array.from(seatSelect.options).some(option => option.value === seatFromUrl);
        if (optionExists) {
            seatSelect.value = seatFromUrl;
            seatSelect.disabled = true;
            console.log(`席番号 ${seatFromUrl} がURLから自動入力されました。`);
        } else {
            console.warn(`URLで指定された席番号「${seatFromUrl}」は選択肢に存在しません。`);
        }
    }

    const SUB_CATEGORIES = subCategoriesDataElement ? JSON.parse(subCategoriesDataElement.textContent) : {};

    // --- 待機人数を定期的に更新する機能 ---
    window.updatePendingCount = function() {
        fetch(`${urlPrefix}/api/count`)
            .then(response => response.json())
            .then(data => {
                if (pendingCountSpan) pendingCountSpan.textContent = data.count;
                if (pendingCountSpanList) pendingCountSpanList.textContent = data.count;
            })
            .catch(error => console.error('人数取得エラー:', error));
    }

    // --- 小区分 (単元) を動的に更新する機能 ---
    window.updateSubCategories = function() {
        if (!subjectSelect || !subCategorySelect) return;
        const selectedSubject = subjectSelect.value;
        subCategorySelect.innerHTML = ''; 
        if (selectedSubject && SUB_CATEGORIES[selectedSubject]) {
            let defaultOption = document.createElement('option');
            defaultOption.value = "";
            defaultOption.textContent = "選択してください";
            subCategorySelect.appendChild(defaultOption);
            SUB_CATEGORIES[selectedSubject].forEach(category => {
                const option = document.createElement('option');
                option.value = category;
                option.textContent = category;
                subCategorySelect.appendChild(option);
            });
            subCategorySelect.disabled = false;
        } else {
            let placeholderOption = document.createElement('option');
            placeholderOption.value = "";
            placeholderOption.textContent = "まず質問内容を選択してください";
            subCategorySelect.appendChild(placeholderOption);
            subCategorySelect.disabled = true;
        }
    }
    
    // --- 写真複数添付機能 ---
    if (formElement && photoInput && imagePreviewContainer) {
        const dataTransfer = new DataTransfer();

        photoInput.addEventListener('change', function() {
            for (const file of this.files) {
                dataTransfer.items.add(file);
                createPreview(file);
            }
            this.value = ''; 
        });

        const createPreview = (file) => {
            const reader = new FileReader();
            reader.onload = function(e) {
                const previewItem = document.createElement('div');
                previewItem.className = 'preview-item';
                const img = document.createElement('img');
                img.src = e.target.result;
                const deleteBtn = document.createElement('button');
                deleteBtn.className = 'delete-btn';
                deleteBtn.innerHTML = '&times;';
                deleteBtn.addEventListener('click', function() {
                    removeFile(file.name);
                    previewItem.remove();
                });
                previewItem.appendChild(img);
                previewItem.appendChild(deleteBtn);
                imagePreviewContainer.appendChild(previewItem);
            };
            reader.readAsDataURL(file);
        };

        const removeFile = (fileName) => {
            const newFiles = new DataTransfer();
            for (const file of dataTransfer.files) {
                if (file.name !== fileName) {
                    newFiles.items.add(file);
                }
            }
            dataTransfer.clearData();
            for (const file of newFiles.files) {
                dataTransfer.items.add(file);
            }
        };

        formElement.addEventListener('submit', function(event) {
            event.preventDefault();
            const seatSelectInput = document.getElementById('seat_num');
            const wasSeatDisabled = seatSelectInput && seatSelectInput.disabled;
            if (wasSeatDisabled) {
                seatSelectInput.disabled = false;
            }

            let isValid = true;
            let validationMessage = '';
            formElement.querySelectorAll('[required]:not([style*="display: none"])').forEach(field => {
                if (!field.value) {
                    isValid = false;
                    field.style.border = '2px solid red'; 
                } else {
                    field.style.border = ''; 
                }
            });
            if (!isValid) validationMessage = '「*」が付いている項目は必ず入力（または選択）してください！';

            const problemNumInput = document.getElementById('problem_num');
            if (problemNumInput && problemNumInput.value) {
                if (!/^[0-9,()\s]+$/.test(problemNumInput.value)) {
                    isValid = false;
                    problemNumInput.style.border = '2px solid red';
                    validationMessage += (validationMessage ? '\n' : '') + '問題番号には、数字, カンマ(,), カッコ(), 空白しか使用できません。';
                } else {
                    problemNumInput.style.border = '';
                }
            }
            
            if (!isValid) {
                if (wasSeatDisabled) {
                    seatSelectInput.disabled = true;
                }
                alert(validationMessage);
                return;
            }
            
            const formData = new FormData(formElement);
            if (wasSeatDisabled) {
                seatSelectInput.disabled = true;
            }

            formData.delete('photo');
            for (const file of dataTransfer.files) {
                formData.append('photo', file);
            }

            fetch(formElement.action, {
                method: 'POST',
                body: formData
            })
            .then(response => {
                if(response.redirected){
                    window.location.href = response.url;
                } else {
                    return response.text().then(text => console.error('送信エラー:', text));
                }
            })
            .catch(error => console.error('フォーム送信エラー:', error));
        });
    }

    if (subjectSelect) { 
        subjectSelect.addEventListener('change', window.updateSubCategories);
    }

    // --- 時計を更新する機能 ---
    window.updateClock = function() {
        if (!clockElement) return; 
        const now = new Date();
        const year = now.getFullYear();
        const month = String(now.getMonth() + 1).padStart(2, '0');
        const day = String(now.getDate()).padStart(2, '0');
        const hours = String(now.getHours()).padStart(2, '0');
        const minutes = String(now.getMinutes()).padStart(2, '0');
        const seconds = String(now.getSeconds()).padStart(2, '0');
        const dateTimeString = `${year}/${month}/${day} ${hours}:${minutes}:${seconds}`;
        clockElement.textContent = dateTimeString;
    }

    // --- 初期呼び出しと定期実行 ---
    window.updatePendingCount(); 
    window.updateClock();        
    setInterval(window.updatePendingCount, 1000);
    setInterval(window.updateClock, 1000);      

    // --- 一覧画面の「済」チェックボックス用 ---
    document.querySelectorAll('.done-checkbox').forEach(checkbox => {
        checkbox.addEventListener('change', function() {
            const questionId = this.dataset.id;
            const row = this.closest('tr');
            if (!row) return;
            fetch(`${urlPrefix}/api/mark_done/${questionId}`, { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        row.classList.add('done-row');
                        this.disabled = true;
                        if (typeof window.updatePendingCount === "function") window.updatePendingCount(); 
                    } else {
                        alert('更新に失敗しました。');
                        this.checked = false; 
                    }
                })
                .catch(error => {
                    console.error('「済」処理エラー:', error);
                    alert('「済」処理中にエラーが発生しました。');
                    this.checked = false; 
                });
        });
    });
    
    if (typeof window.updateSubCategories === "function") window.updateSubCategories(); 

    // --- 一括削除フォームの処理 ---
    if (bulkDeleteForm && bulkDeleteButton) { 
        const questionCheckboxes = bulkDeleteForm.querySelectorAll('.question-checkbox');
        bulkDeleteButton.addEventListener('click', function(event) {
            const selectedCheckboxes = bulkDeleteForm.querySelectorAll('.question-checkbox:checked');
            if (selectedCheckboxes.length === 0) {
                alert('削除する質問が選択されていません。');
                event.preventDefault(); 
                return;
            }
            if (!confirm(`選択された ${selectedCheckboxes.length} 件の質問を本当に削除しますか？\nこの操作は元に戻せません。`)) {
                event.preventDefault(); 
            }
        });
        if (selectAllCheckbox) { 
            selectAllCheckbox.addEventListener('change', function() {
                questionCheckboxes.forEach(checkbox => {
                    if (!checkbox.disabled) checkbox.checked = selectAllCheckbox.checked;
                });
            });
            questionCheckboxes.forEach(checkbox => {
                checkbox.addEventListener('change', function() {
                    if (!this.checked) {
                        selectAllCheckbox.checked = false;
                    } else {
                        let allChecked = true;
                        questionCheckboxes.forEach(cb => {
                            if (!cb.disabled && !cb.checked) allChecked = false;
                        });
                        selectAllCheckbox.checked = allChecked;
                    }
                });
            });
        }
    }

    // ★★★ 変更点: 音声ロック解除のリスナーをここに追加 ★★★
    document.body.addEventListener('click', unlockAudio);
    document.body.addEventListener('touchstart', unlockAudio);

    // 通知機能を初期化
    initializeNotifier();
});

window.addEventListener('pageshow', function(event) {
    handleFormResetLogic();
    // ページがキャッシュから復元された場合に、通知機能を再初期化する
    if (event.persisted) {
        console.log("ページがキャッシュから復元されたため、通知機能を再初期化します。");
        initializeNotifier();
    }
});


// --- グローバル通知機能（安定版＋音声機能） ---
function initializeNotifier() {
    // 既存のタイマーがあれば停止する（二重実行防止）
    if (window.notifierIntervalId) {
        clearInterval(window.notifierIntervalId);
    }

    if (!window.isUserLoggedIn) {
        return; // 未ログイン時は実行しない
    }

    let lastKnownId = 0;
    let newQuestionCount = 0;
    let isInitialLoad = true;
    const originalTitle = document.title;

    const removeNotificationBanner = () => {
        const banner = document.getElementById('new-question-banner');
        if (banner) banner.remove();
        document.body.style.paddingTop = '0';
        document.title = originalTitle;
        newQuestionCount = 0;
    };

    const createOrUpdateNotificationBanner = () => {
        let banner = document.getElementById('new-question-banner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'new-question-banner';
            banner.style.cssText = 'background-color: #ffc107; color: black; text-align: center; padding: 10px; font-weight: bold; position: fixed; top: 0; left: 0; width: 100%; z-index: 1000;';
            document.body.insertBefore(banner, document.body.firstChild);
            document.body.style.paddingTop = banner.offsetHeight + 'px';
        }
        const currentPagePath = window.location.pathname;
        if (currentPagePath === '/list' || currentPagePath.startsWith('/list/')) {
            banner.innerHTML = '新しい質問が届きました。 <button class="button" style="margin-left: 15px;">ページを更新</button>';
            banner.querySelector('button').onclick = (e) => {
                e.stopPropagation();
                window.location.reload(); 
            };
        } else {
            banner.innerHTML = `新しい質問が届きました。 <a href="/list" class="button">一覧で確認</a>`;
            banner.style.cursor = 'pointer';
            banner.onclick = () => {
                window.location.href = '/list';
            };
        }
    };

    const checkForNewQuestions = () => {
        const endpoint = `${urlPrefix}/api/check_new_questions`;
        const url = isInitialLoad ? `${endpoint}?since_id=0` : `${endpoint}?since_id=${lastKnownId}`;

        fetch(url)
            .then(response => response.json())
            .then(data => {
                if (isInitialLoad) {
                    lastKnownId = data.latest_id;
                    isInitialLoad = false;
                    console.log(`通知機能：初期化完了。現在の最新IDは ${lastKnownId} です。`);
                    return;
                }

                if (data.new_question_count > 0) {
                    newQuestionCount += data.new_question_count;
                    lastKnownId = data.latest_id;
                    
                    document.title = `新しい質問 (${newQuestionCount})`;
                    createOrUpdateNotificationBanner();
                    
                    // ★★★ 変更点: グローバル変数を使って再生を試みる ★★★
                    if (isAudioUnlocked) {
                        notificationSound.play().catch(error => {
                            console.warn("音声の再生に失敗しました。", error);
                        });
                    } else {
                        console.log("音声再生がまだ許可されていません。ユーザーによる操作が必要です。");
                    }
                }
            })
            .catch(error => console.error('新着質問のチェック中にエラー:', error));
    };

    // 1秒ごとに新着質問をチェック
    window.notifierIntervalId = setInterval(checkForNewQuestions, 1000);
}
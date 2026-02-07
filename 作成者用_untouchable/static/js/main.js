// --- DOMContentLoaded ---
document.addEventListener('DOMContentLoaded', () => {
    initializePage();
});

// --- グローバル変数・定数 ---
let studentsData = {};
let currentAttendees = [];
let isCalendarOpen = false;
let lastScannedId = null; 
const exitTimers = {}; // { log_id: timerId }

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
    toastContainer: document.getElementById('toast-container')
};

/**
 * @function initializePage
 * @description ページの初期化を行うメイン関数
 */
function initializePage() {
    updateTime();
    setInterval(updateTime, 1000);
    
    resetSelect(dom.gradeSelect, "");
    resetSelect(dom.classSelect, "");
    resetSelect(dom.numberSelect, "");
    resetSelect(dom.seatSelect, "");

    fetchInitialData();
    setupEventListeners();

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
        populateGradeSelect();
        renderAttendanceTable();
    } catch (error) {
        console.error('初期データの読み込みに失敗しました:', error);
        showToast("エラー: サーバーから情報を取得できませんでした。");
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

        dom.exitAllBtn.addEventListener('click', handleExitAll);
        dom.createReportBtn.addEventListener('click', handleCreateReport);
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
    // これにより、入退室の基本メッセージにも常に称号の色が適用される
    showToast(result.message, result.rank);
    
    if (response.ok) {
        if (result.achievement && result.achievement.student_message) {
            setTimeout(() => {
                // 補足: achievementの中のrankではなく、result直下の最新のrankを渡すように統一します
                showToast(result.achievement.student_message, result.rank);
            }, 750);
        }
        await fetchInitialData();
        // resetAllSelectors(); // 【修正】削除: 送信ボタン押下時に即時リセットするため、ここでは行わない
    }
}


async function handleManualEntry() {
    const student = getSelectedStudent();
    const seatNumber = dom.seatSelect.value;
    if (!student || !seatNumber) return showToast("生徒と座席を選択してください。");

    // 【修正】API通信を待たずに即座にUIをリセットし、次の入力を可能にする
    resetAllSelectors();

    try {
        const response = await fetch('/api/check_in', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ system_id: student.system_id, seat_number: seatNumber })
        });
        await processApiResponse(response);
    } catch (error) {
        console.error('入室処理エラー:', error);
        showToast("エラー: 入室処理中に問題が発生しました。");
    }
}

async function handleManualExit() {
    const student = getSelectedStudent();
    if (!student) return showToast("生徒を選択してください。");

    // 【修正】API通信を待たずに即座にUIをリセット
    resetAllSelectors();

    const exitTime = new Date().toISOString();
    finalizeExit(student.current_log_id, student.system_id, exitTime);
}

async function handleQrInput(event) {
    // イベント種別判定は呼び出し元(processInput)で行うため削除

    // 入力値を取得
    const rawId = event.target.value;
    if (!rawId) return; // 空の場合は無視

    const normalizedId = normalizeSystemId(rawId);
    
    // 正規化後に7桁の数字になっているかチェック
    if (!/^\d{7}$/.test(normalizedId)) {
        // デバッグ用にどのような変換結果になったかも含めて表示(運用開始後は削除しても可)
        showToast(`無効なID形式です。(認識結果: ${normalizedId || '解析不能'})`);
        event.target.value = '';
        return;
    }

    event.target.value = ''; 
    // 【修正】QR読み取り時も即座に画面の選択状態をリセットする
    resetAllSelectors();

    if (normalizedId === lastScannedId) return; 
    lastScannedId = normalizedId;
    setTimeout(() => { lastScannedId = null; }, 5000);
    try {
        const response = await fetch('/api/qr_process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ system_id: normalizedId })
        });
        await processApiResponse(response);
    } catch (error) {
        console.error('QR処理エラー:', error);
        showToast("エラー: QR処理中に問題が発生しました。");
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
    try {
        const response = await fetch('/api/check_out', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ log_id: logId, system_id: systemId, exit_time: exitTime })
        });
        await processApiResponse(response);
    } catch (error) {
        console.error('退室処理エラー:', error);
        showToast("エラー: 退室処理中に問題が発生しました。");
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
    dom.attendanceTableBody.innerHTML = ''; 
    const list = APP_MODE === 'students' ? currentAttendees.filter(s => s.seat_number) : currentAttendees;
    if (list.length === 0) {
        const row = dom.attendanceTableBody.insertRow();
        const cell = row.insertCell();
        cell.colSpan = 9;
        cell.textContent = "本日、まだ入室者はいません。";
        cell.style.textAlign = 'center';
    } else {
        list.forEach((student, index) => {
            const row = dom.attendanceTableBody.insertRow();
            if(student.exit_time) row.classList.add('exited-row');
            row.insertCell().textContent = index + 1;
            const gradeNames = {1:'中1', 2:'中2', 3:'中3', 4:'高1', 5:'高2', 6:'高3'};
            row.insertCell().textContent = gradeNames[student.grade] || student.grade;
            row.insertCell().textContent = student.class;
            row.insertCell().textContent = student.student_number;
            row.insertCell().textContent = student.seat_number || 'QR';
            row.insertCell().textContent = student.name;
            row.insertCell().textContent = new Date(student.entry_time).toLocaleTimeString('ja-JP');
            const durationCell = row.insertCell();
            durationCell.dataset.entryTime = student.entry_time;
            updateDuration(durationCell, student.exit_time);
            const actionCell = row.insertCell();
            actionCell.classList.add('action-cell');
            if (student.exit_time) {
                actionCell.textContent = new Date(student.exit_time).toLocaleTimeString('ja-JP');
            } else {
                 actionCell.innerHTML = `<button class="exit-list-btn" data-log-id="${student.log_id}" data-system-id="${student.system_id}">退室</button>`;
            }
        });
        startDurationTimers();
    }
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
function startDurationTimers() {
    if (durationInterval) clearInterval(durationInterval);
    durationInterval = setInterval(() => {
        const durationCells = dom.attendanceTableBody.querySelectorAll('td[data-entry-time]');
        durationCells.forEach(cell => {
            if (!cell.dataset.exitTime) updateDuration(cell);
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
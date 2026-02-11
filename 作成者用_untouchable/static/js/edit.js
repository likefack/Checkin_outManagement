document.addEventListener('DOMContentLoaded', () => {
    initializeEditPage();
});

// --- グローバル変数・状態管理 ---
const GRADE_MAP = {
    1: '中1',
    2: '中2',
    3: '中3',
    4: '高1',
    5: '高2',
    6: '高3'
};
let allStudents = [];
let studentsDataNested = {};
let currentPage = 1;
const logsPerPage = 100;
let currentSort = { column: 'id', direction: 'desc' };
let totalLogsCount = 0;
let entryTimePicker = null;
let exitTimePicker = null;
// 連続操作防止用フラグ
let isNavigating = false; // ページ遷移中
let isLoading = false;    // データ取得中
let globalEventSource = null; // SSE接続管理用

// ページ離脱時にリソースを解放する
window.addEventListener('beforeunload', () => {
    if (globalEventSource) {
        globalEventSource.close();
        globalEventSource = null;
    }
});

// --- DOM要素の取得 ---
const dom = {
    filterPeriod: document.getElementById('filter-period'),
    filterName: document.getElementById('filter-name'),
    filterGrade: document.getElementById('filter-grade'),
    filterClass: document.getElementById('filter-class'),
    filterNumber: document.getElementById('filter-number'),
    filterBtn: document.getElementById('filter-btn'),
    resetFilterBtn: document.getElementById('reset-filter-btn'),
    addNewLogBtn: document.getElementById('add-new-log-btn'),
    logsTableBody: document.getElementById('logs-table-body'),
    tableHeaders: document.querySelectorAll('th[data-sort]'),
    pageInfo: document.getElementById('page-info'),
    prevPageBtn: document.getElementById('prev-page-btn'),
    nextPageBtn: document.getElementById('next-page-btn'),
    modal: document.getElementById('edit-modal'),
    modalTitle: document.getElementById('modal-title'),
    editForm: document.getElementById('edit-form'),
    editLogId: document.getElementById('edit-log-id'),
    modalGradeSelect: document.getElementById('modal-grade-select'),
    modalClassSelect: document.getElementById('modal-class-select'),
    modalNumberSelect: document.getElementById('modal-number-select'),
    modalStudentName: document.getElementById('modal-student-name'),
    editSeatNumber: document.getElementById('edit-seat-number'),
    editEntryTime: document.getElementById('edit-entry-time'),
    editExitTime: document.getElementById('edit-exit-time'),
    modalCancelBtn: document.getElementById('modal-cancel-btn'),
    modalSaveBtn: document.getElementById('modal-save-btn'),
};

/**
 * ページの初期化
 */
async function initializeEditPage() {
    setupSSE();
    setupEventListeners();
    flatpickr(dom.filterPeriod, { mode: "range", dateFormat: "Y-m-d", locale: "ja" });

    // モーダル内の日時入力欄にflatpickrを適用
    const flatpickrOptions = {
        enableTime: true,           // 時間の選択を有効化
        dateFormat: "Y/m/d H:i",    // 表示形式を「年/月/日 時:分」に設定 (曜日なし)
        locale: "ja",               // 日本語化
        time_24hr: true,            // 24時間表示
        position: 'above' // カレンダーを常に入力欄の上に表示する
    };
    entryTimePicker = flatpickr(dom.editEntryTime, flatpickrOptions);
    exitTimePicker = flatpickr(dom.editExitTime, flatpickrOptions);
    
    // 座席選択肢の生成
    populateEditSeatSelect();

    // ヘッダーの「戻る」ボタンの制御
    const backLink = document.getElementById('header-back-link');
    if (backLink) {
        backLink.addEventListener('click', (e) => {
            if (isNavigating) {
                e.preventDefault();
                return;
            }
            isNavigating = true;
            // 視覚的フィードバック（任意）
            backLink.style.opacity = '0.5';
        });
    }

    await fetchLogs();
}

/**
 * イベントリスナーをまとめて設定
 */
function setupEventListeners() {
    dom.filterBtn.addEventListener('click', () => { currentPage = 1; fetchLogs(); });
    dom.resetFilterBtn.addEventListener('click', resetFilters);
    dom.tableHeaders.forEach(header => header.addEventListener('click', handleSort));
    dom.prevPageBtn.addEventListener('click', () => changePage(-1));
    dom.nextPageBtn.addEventListener('click', () => changePage(1));
    dom.addNewLogBtn.addEventListener('click', () => openModal());
    dom.modalCancelBtn.addEventListener('click', closeModal);
    dom.editForm.addEventListener('submit', handleFormSubmit);
    dom.logsTableBody.addEventListener('click', handleTableActions);
    dom.modalGradeSelect.addEventListener('change', onModalGradeChange);
    dom.modalClassSelect.addEventListener('change', onModalClassChange);
    dom.modalNumberSelect.addEventListener('change', onModalNumberChange);
}

/**
 * サーバーからログデータを取得して描画
 */
async function fetchLogs() {
    if (isLoading) return; // 既にロード中なら何もしない
    isLoading = true;
    
    // UI操作を一時的に無効化（オプション）
    dom.logsTableBody.style.opacity = '0.5';

    const params = new URLSearchParams({ page: currentPage, per_page: logsPerPage, sort: currentSort.column, dir: currentSort.direction });
    const period = dom.filterPeriod.value;
    // 日本語ロケールの区切り文字 " から " を使用
    if (period.includes(' から ')) {
        const [start, end] = period.split(' から ');
        // 分割した start と end の値が存在すれば、それぞれのパラメータに追加
        if (start) {
            params.append('start', start.trim()); // 前後の空白を削除
        }
        if (end) {
            params.append('end', end.trim());     // 前後の空白を削除
        }
    } else if (period) { // 単一日選択の場合
        // 単一日でも start と end の両方に同じ日付を設定
        params.append('start', period);
        params.append('end', period);
    }
    if(dom.filterName.value) params.append('name', dom.filterName.value);
    if(dom.filterGrade.value) params.append('grade', dom.filterGrade.value);
    if(dom.filterClass.value) params.append('class', dom.filterClass.value);
    if(dom.filterNumber.value) params.append('number', dom.filterNumber.value);
    try {
        const response = await fetch(`/api/logs?${params.toString()}`);
        if (!response.ok) throw new Error('サーバーからの応答がありません。');
        const data = await response.json();
        allStudents = data.students;
        buildNestedStudentsData(); 
        renderTable(data.logs);
        updatePagination(data.total);
        if (dom.filterGrade.options.length <= 1) {
            populateFilterSelects(data.students);
        }
    } catch (error) {
        console.error("ログの取得に失敗:", error);
        dom.logsTableBody.innerHTML = `<tr><td colspan="10" style="text-align:center; color:red;">データの読み込みに失敗しました。</td></tr>`;
    } finally {
        isLoading = false;
        dom.logsTableBody.style.opacity = '1';
    }
}

/**
 * テーブルの描画
 */
function renderTable(logs) {
    dom.logsTableBody.innerHTML = '';
    if (logs.length === 0) {
        dom.logsTableBody.innerHTML = `<tr><td colspan="10" style="text-align:center;">該当する記録はありません。</td></tr>`;
        return;
    }
    logs.forEach(log => {
        const entryDate = log.entry_time ? new Date(log.entry_time) : null;
        const exitDate = log.exit_time ? new Date(log.exit_time) : null;
        let durationText = '---';
        if (entryDate && exitDate) {
            const diffMinutes = Math.round((exitDate - entryDate) / 60000);
            const hours = Math.floor(diffMinutes / 60);
            const minutes = diffMinutes % 60;
            durationText = `${hours}時間 ${minutes}分`;
        }
        const row = dom.logsTableBody.insertRow();
        // 座席番号がない場合は 'QR' と表示
        const seatDisplay = log.seat_number ? log.seat_number : 'QR';
        row.innerHTML = `
            <td>${log.id}</td>
            <td>${entryDate ? entryDate.toLocaleDateString('ja-JP') : ''}</td>
            <td>${GRADE_MAP[log.grade] || log.grade || ''}</td>
            <td>${log.class || ''}</td>
            <td>${log.student_number || ''}</td>
            <td>${log.name || ''}</td>
            <td>${seatDisplay}</td>
            <td>${entryDate ? entryDate.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' }) : ''}</td>
            <td>${exitDate ? exitDate.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' }) : '（未退室）'}</td>
            <td>${durationText}</td>
            <td class="action-buttons">
                <button class="edit-btn" data-log-id="${log.id}">編集</button>
                <button class="delete-btn danger-btn" data-log-id="${log.id}">削除</button>
            </td>
        `;
    });
}

/**
 * ページネーションの更新
 */
function updatePagination(total) {
    totalLogsCount = total;
    const totalPages = Math.ceil(totalLogsCount / logsPerPage) || 1;
    dom.pageInfo.textContent = `${currentPage} / ${totalPages} ページ (${totalLogsCount}件)`;
    dom.prevPageBtn.disabled = currentPage === 1;
    dom.nextPageBtn.disabled = currentPage >= totalPages;
}

function changePage(direction) { currentPage += direction; fetchLogs(); }
function resetFilters() {
    dom.filterPeriod.value = '';
    dom.filterName.value = '';
    dom.filterGrade.value = '';
    dom.filterClass.value = '';
    dom.filterNumber.value = '';
    if (dom.filterPeriod._flatpickr) {
        dom.filterPeriod._flatpickr.clear();
    }
    currentPage = 1;
    fetchLogs();
}
function handleSort(event) {
    const column = event.target.dataset.sort;
    if (currentSort.column === column) {
        currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        currentSort.column = column;
        currentSort.direction = 'desc';
    }
    dom.tableHeaders.forEach(h => h.classList.remove('sorted-asc', 'sorted-desc'));
    event.target.classList.add(currentSort.direction === 'asc' ? 'sorted-asc' : 'sorted-desc');
    currentPage = 1;
    fetchLogs();
}
async function handleTableActions(event) {
    const target = event.target;
    const logId = target.dataset.logId;
    if (!logId) return;
    if (target.classList.contains('edit-btn')) {
        const response = await fetch(`/api/logs?id=${logId}`);
        const data = await response.json();
        if(data.logs.length > 0) openModal(data.logs[0]);
    } else if (target.classList.contains('delete-btn')) {
        if (confirm(`ID: ${logId} の記録を本当に削除しますか？\nこの操作は取り消せません。`)) {
            try {
                const response = await fetch(`/api/logs/${logId}`, { method: 'DELETE' });
                const result = await response.json();
                alert(result.message);
                if (response.ok) fetchLogs();
            } catch (error) {
                alert("削除中にエラーが発生しました。");
            }
        }
    }
}
//modal関連のヘルパー関数群
// edit.js

// edit.js

/**
 * モーダルの表示
 */
async function openModal(log = null) {
    dom.editForm.reset();
    populateModalGradeSelect(); 
    
    if (log) { // 編集の場合
        dom.modalTitle.textContent = "記録の編集";
        dom.editLogId.value = log.id;
        dom.modalGradeSelect.value = log.grade;
        await onModalGradeChange();
        dom.modalClassSelect.value = log.class;
        await onModalClassChange();
        dom.modalNumberSelect.value = log.student_number;
        await onModalNumberChange();
        
        // 座席番号をセット
        dom.editSeatNumber.value = log.seat_number || '';

        // 記録済みの入室時刻をセット（なければ現在時刻）
        const entryDate = log.entry_time ? new Date(log.entry_time) : new Date();
        entryTimePicker.setDate(entryDate, true);
        
        // 記録済みの退室時刻があればセットし、なければ現在時刻をセット
        const exitDate = log.exit_time ? new Date(log.exit_time) : new Date();
        exitTimePicker.setDate(exitDate, true);

     } else { // 新規追加の場合
        dom.modalTitle.textContent = "新規記録の追加";
        dom.editLogId.value = '';
        dom.modalGradeSelect.value = '';
        onModalGradeChange();

        // 座席番号と日付をクリアする
        dom.editSeatNumber.value = '';
        entryTimePicker.clear();
        exitTimePicker.clear();
    }
    
    dom.modal.style.display = 'flex';
}

function closeModal() {
    dom.modal.style.display = 'none';
     // モーダルを閉じる際にも日付をクリアする
    entryTimePicker.clear();
    exitTimePicker.clear();
}

/**
 * フォーム送信（保存）処理
 */
async function handleFormSubmit(event) {
    event.preventDefault();
    const logId = dom.editLogId.value;
    
    const grade = dom.modalGradeSelect.value;
    const classNum = dom.modalClassSelect.value;
    const number = dom.modalNumberSelect.value;
    const student = studentsDataNested[grade]?.[classNum]?.[number];

    if (!student) {
        alert("生徒が正しく選択されていません。");
        return;
    }
     // flatpickrから選択された日付オブジェクトを取得
    const entryTime = entryTimePicker.selectedDates[0];
    const exitTime = exitTimePicker.selectedDates[0];

    // 新しいヘルパー関数を使って、サーバーが要求する 'YYYY-MM-DD HH:MM:SS' 形式に変換
    const entryTimeVal = formatDateForServer(entryTime);
    const exitTimeVal = formatDateForServer(exitTime);

    const logData = {
        system_id: student.system_id,
        entry_time: entryTimeVal,
        exit_time: exitTimeVal,
        seat_number: dom.editSeatNumber.value || null
    };
    
    const url = logId ? `/api/logs/${logId}` : '/api/logs';
    const method = logId ? 'PUT' : 'POST';

    // 二重送信防止：ボタンを無効化
    dom.modalSaveBtn.disabled = true;
    dom.modalSaveBtn.textContent = '保存中...';

    try {
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(logData)
        });
        const result = await response.json();
        alert(result.message);
        if (response.ok) {
            closeModal();
            fetchLogs();
        }
    } catch (error) {
        alert("保存中にエラーが発生しました。");
    } finally {
        // ボタンを再度有効化
        dom.modalSaveBtn.disabled = false;
        dom.modalSaveBtn.textContent = '保存';
    }
}

// 古いformatDateForInput関数は不要になったので、全体を新しいヘルパー関数に差し替える
/**
 * JavaScriptのDateオブジェクトを 'YYYY-MM-DD HH:MM:SS' 形式の文字列に変換する
 * @param {Date} date - 変換するDateオブジェクト
 * @returns {string|null} - フォーマットされた文字列、またはdateがなければnull
 */
function formatDateForServer(date) {
    if (!date) return null;
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    const h = String(date.getHours()).padStart(2, '0');
    const i = String(date.getMinutes()).padStart(2, '0');
    const s = String(date.getSeconds()).padStart(2, '0');
    return `${y}-${m}-${d} ${h}:${i}:${s}`;
}
// --- 生徒選択・ドロップダウン関連のヘルパー関数群 ---
function buildNestedStudentsData() {
    studentsDataNested = {};
    allStudents.forEach(s => {
        if (!studentsDataNested[s.grade]) studentsDataNested[s.grade] = {};
        if (!studentsDataNested[s.grade][s.class]) studentsDataNested[s.grade][s.class] = {};
        studentsDataNested[s.grade][s.class][s.student_number] = s;
    });
}
function populateModalGradeSelect() {
    dom.modalGradeSelect.innerHTML = '<option value="">学年</option>';
    const grades = Object.keys(studentsDataNested).sort((a,b)=>a-b);
    grades.forEach(g => dom.modalGradeSelect.add(new Option(GRADE_MAP[g] || g, g)));
}
async function onModalGradeChange() {
    const grade = dom.modalGradeSelect.value;
    dom.modalClassSelect.innerHTML = '<option value="">組</option>';
    dom.modalNumberSelect.innerHTML = '<option value="">番号</option>';
    dom.modalClassSelect.disabled = true;
    dom.modalNumberSelect.disabled = true;
    dom.modalStudentName.textContent = '';
    if (grade && studentsDataNested[grade]) {
        const classes = Object.keys(studentsDataNested[grade]).sort((a,b)=>a-b);
        classes.forEach(c => dom.modalClassSelect.add(new Option(c, c)));
        dom.modalClassSelect.disabled = false;
    }
}
async function onModalClassChange() {
    const grade = dom.modalGradeSelect.value;
    const classNum = dom.modalClassSelect.value;
    dom.modalNumberSelect.innerHTML = '<option value="">番号</option>';
    dom.modalNumberSelect.disabled = true;
    dom.modalStudentName.textContent = '';
    if (grade && classNum && studentsDataNested[grade]?.[classNum]) {
        const numbers = Object.keys(studentsDataNested[grade][classNum]).sort((a,b)=>a-b);
        numbers.forEach(n => dom.modalNumberSelect.add(new Option(n, n)));
        dom.modalNumberSelect.disabled = false;
    }
}
async function onModalNumberChange() {
    const grade = dom.modalGradeSelect.value;
    const classNum = dom.modalClassSelect.value;
    const number = dom.modalNumberSelect.value;
    dom.modalStudentName.textContent = '';
    if (grade && classNum && number) {
        const student = studentsDataNested[grade]?.[classNum]?.[number];
        if (student) {
            dom.modalStudentName.textContent = student.name;
        }
    }
}
function populateFilterSelects(students) {
    const grades = [...new Set(students.map(s => s.grade))].sort((a,b)=>a-b);
    const classes = [...new Set(students.map(s => s.class))].sort((a,b)=>a-b);
    const numbers = [...new Set(students.map(s => s.student_number))].sort((a,b)=>a-b);
    grades.forEach(g => dom.filterGrade.add(new Option(GRADE_MAP[g] || g, g)));
    classes.forEach(c => dom.filterClass.add(new Option(c, c)));
    numbers.forEach(n => dom.filterNumber.add(new Option(n, n)));
}

/**
 * 編集モーダルの座席選択肢を生成する
 * main.jsと同様の選択肢を提供する
 */
function populateEditSeatSelect() {
    const select = dom.editSeatNumber;
    select.innerHTML = ''; // クリア

    // 未指定（QR）用のオプション
    const defaultOption = document.createElement('option');
    defaultOption.value = "";
    defaultOption.textContent = "QR";
    select.appendChild(defaultOption);

    // 臨時教室など
    const extraRooms = ['座席なし', '223教室', '224教室', '225教室'];
    extraRooms.forEach(room => {
        const option = document.createElement('option');
        option.value = room;
        option.textContent = room;
        select.appendChild(option);
    });

    // 座席番号 1〜72
    for (let i = 1; i <= 72; i++) {
        const option = document.createElement('option');
        option.value = i;
        option.textContent = i;
        select.appendChild(option);
    }
}

/**
 * SSE接続を設定し、更新通知を受け取ったらリストを再取得する
 */
function setupSSE() {
    if (globalEventSource) {
        globalEventSource.close();
    }
    globalEventSource = new EventSource('/api/stream');
    
    globalEventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'update') {
            console.log("更新通知を受信。編集画面のリストを更新します。");
            fetchLogs();
        }
    };
}


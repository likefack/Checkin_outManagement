document.addEventListener('DOMContentLoaded', () => {
    initializeEditPage();
});

// --- グローバル変数・状態管理 ---
let allStudents = [];
let studentsDataNested = {};
let currentPage = 1;
const logsPerPage = 100;
let currentSort = { column: 'id', direction: 'desc' };
let totalLogsCount = 0;

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
    editEntryTime: document.getElementById('edit-entry-time'),
    editExitTime: document.getElementById('edit-exit-time'),
    modalCancelBtn: document.getElementById('modal-cancel-btn'),
    modalSaveBtn: document.getElementById('modal-save-btn'),
};

/**
 * ページの初期化
 */
async function initializeEditPage() {
    setupEventListeners();
    // モーダルではflatpickrを使わないので、フィルター用のみ初期化
    flatpickr(dom.filterPeriod, { mode: "range", dateFormat: "Y-m-d", locale: "ja" });
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
    const params = new URLSearchParams({ page: currentPage, per_page: logsPerPage, sort: currentSort.column, dir: currentSort.direction });
    const period = dom.filterPeriod.value;
    if (period.includes(' to ')) {
        const [start, end] = period.split(' to ');
        params.append('start', start);
        params.append('end', end);
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
        row.innerHTML = `
            <td>${log.id}</td>
            <td>${entryDate ? entryDate.toLocaleDateString('ja-JP') : ''}</td>
            <td>${log.grade || ''}</td>
            <td>${log.class || ''}</td>
            <td>${log.student_number || ''}</td>
            <td>${log.name || ''}</td>
            <td>${entryDate ? entryDate.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' }) : ''}</td>
            <td>${exitDate ? exitDate.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' }) : '（在室中）'}</td>
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
        dom.editEntryTime.value = formatDateForInput(log.entry_time);
        dom.editExitTime.value = formatDateForInput(log.exit_time);
    } else { // 新規追加の場合
        dom.modalTitle.textContent = "新規記録の追加";
        dom.editLogId.value = '';
        dom.modalGradeSelect.value = '';
        onModalGradeChange();
        dom.editEntryTime.value = '';
        dom.editExitTime.value = '';
    }
    dom.modal.style.display = 'flex';
}

function closeModal() {
    dom.modal.style.display = 'none';
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
    
    // ★★★ 修正: datetime-localの値をサーバーが期待する形式に変換 ★★★
    const entryTimeVal = dom.editEntryTime.value ? dom.editEntryTime.value.replace('T', ' ') + ':00' : null;
    const exitTimeVal = dom.editExitTime.value ? dom.editExitTime.value.replace('T', ' ') + ':00' : null;

    const logData = {
        system_id: student.system_id,
        entry_time: entryTimeVal,
        exit_time: exitTimeVal
    };

    const url = logId ? `/api/logs/${logId}` : '/api/logs';
    const method = logId ? 'PUT' : 'POST';

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
    }
}

/**
 * ISO文字列をdatetime-localの入力形式(YYYY-MM-DDTHH:MM)に変換するヘルパー関数
 */
function formatDateForInput(isoString) {
    if (!isoString) return '';
    try {
        const date = new Date(isoString);
        const offset = date.getTimezoneOffset() * 60000;
        const localDate = new Date(date.getTime() - offset);
        return localDate.toISOString().slice(0, 16);
    } catch (e) {
        console.error("Date formatting error:", e);
        return '';
    }
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
    grades.forEach(g => dom.modalGradeSelect.add(new Option(g, g)));
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
    grades.forEach(g => dom.filterGrade.add(new Option(g, g)));
    classes.forEach(c => dom.filterClass.add(new Option(c, c)));
    numbers.forEach(n => dom.filterNumber.add(new Option(n, n)));
}


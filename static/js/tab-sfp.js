/**
 * Tab 3 «SFP/DDM» — фильтры (устройство + только проблемные).
 */

function $(sel) { return document.querySelector(sel); }

function applyFilters() {
    const devFilter = ($('#sfp-filter-device') || {}).value || '';
    const onlyProblems = ($('#sfp-filter-problems') || {}).checked || false;

    document.querySelectorAll('.sfp-device-group').forEach(group => {
        const ip = group.dataset.deviceIp;
        const deviceOk = !devFilter || ip === devFilter;

        // Фильтруем плитки внутри группы
        let visibleCards = 0;
        group.querySelectorAll('.sfp-card').forEach(card => {
            const state = card.dataset.tempState || 'unknown';
            const stateOk = !onlyProblems || state === 'warning' || state === 'alarm';
            const show = deviceOk && stateOk;
            card.style.display = show ? '' : 'none';
            if (show) visibleCards++;
        });

        // Скрываем группу, если нет видимых плиток
        group.style.display = visibleCards > 0 ? '' : 'none';
    });
}

document.addEventListener('DOMContentLoaded', () => {
    const devSel = $('#sfp-filter-device');
    const problemsCb = $('#sfp-filter-problems');

    if (devSel) devSel.addEventListener('change', applyFilters);
    if (problemsCb) problemsCb.addEventListener('change', applyFilters);
});

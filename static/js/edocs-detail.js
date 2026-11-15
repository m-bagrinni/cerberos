document.addEventListener('DOMContentLoaded', function() {
    // Переключение вкладок
    const tabs = document.querySelectorAll('.doc-tab');
    const contents = document.querySelectorAll('.doc-tab-content');
    
    tabs.forEach(tab => {
        tab.addEventListener('click', function() {
            const target = this.dataset.tab;
            
            // Убираем активный класс у всех вкладок
            tabs.forEach(t => {
                t.style.color = 'var(--text-muted)';
                t.style.borderBottom = 'none';
                t.style.fontWeight = 'normal';
            });
            
            // Делаем текущую вкладку активной
            this.style.color = 'var(--primary)';
            this.style.borderBottom = '2px solid var(--primary)';
            this.style.fontWeight = '600';
            
            // Скрываем все контенты
            contents.forEach(c => c.style.display = 'none');
            
            // Показываем нужный
            const content = document.getElementById('tab-' + target);
            if (content) content.style.display = 'block';
        });
    });
    
    // Отправка комментария (AJAX)
    const form = document.getElementById('commentForm');
    if (!form) return;
    
    form.addEventListener('submit', function(e) {
        e.preventDefault();
        const input = document.getElementById('commentInput');
        if (!input) return;
        const text = input.value.trim();
        if (!text) return;
        
        const formData = new FormData();
        formData.append('comment_text', text);
        
        fetch(window.location.pathname, {
            method: 'POST',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': window.CSRF_TOKEN
            },
            body: formData
        })
        .then(async res => {
            const data = await res.json();
            if (data.status === 'ok') {
                const historyContainer = document.getElementById('doc-history');
                if (historyContainer) {
                    historyContainer.innerHTML = data.history_html;
                    historyContainer.scrollTop = historyContainer.scrollHeight;
                } else {
                    window.location.reload();
                }
                input.value = '';
            }
        })
        .catch(err => console.error('EDocs AJAX Error:', err));
    });
});

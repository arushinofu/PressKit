// Admin panel JavaScript - Simple Auth

// Handle login form submission
document.getElementById('loginForm')?.addEventListener('submit', async function(e) {
    e.preventDefault();

    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value.trim();

    if (!username || !password) {
        showAlert('Заполните все поля', 'error');
        return;
    }

    try {
        const response = await fetch('/admin/auth', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            credentials: 'include',
            body: JSON.stringify({ username, password })
        });

        const data = await response.json();

        if (data.success) {
            // Перенаправляем на dashboard
            window.location.href = data.redirect;
        } else {
            showAlert(data.message || 'Ошибка входа', 'error');
        }
    } catch (error) {
        console.error('Login error:', error);
        showAlert('Ошибка соединения с сервером', 'error');
    }
});

function showAlert(message, type) {
    const alertDiv = document.getElementById('loginAlert');
    if (alertDiv) {
        alertDiv.innerHTML = `<div class="alert alert-${type}">${message}</div>`;
        setTimeout(() => {
            alertDiv.innerHTML = '';
        }, 5000);
    }
}

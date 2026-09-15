/* Tela de login — script simples, sem módulos, para carregar instantaneamente. */
(function () {
  var form = document.getElementById('formLogin');
  var erro = document.getElementById('loginErro');
  var botao = document.getElementById('btnEntrar');

  function mostrarErro(mensagem) {
    erro.textContent = mensagem;
    erro.classList.remove('hidden');
  }

  form.addEventListener('submit', async function (evento) {
    evento.preventDefault();
    erro.classList.add('hidden');

    var email = document.getElementById('email').value.trim();
    var senha = document.getElementById('senha').value;
    if (!email || !senha) { mostrarErro('Preencha e-mail e senha.'); return; }

    botao.disabled = true;
    botao.textContent = 'Entrando...';
    try {
      var resposta = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ email: email, senha: senha }),
      });
      var corpo = await resposta.json().catch(function () { return {}; });
      if (!resposta.ok) { mostrarErro(corpo.erro || 'Não foi possível entrar.'); return; }
      window.location.href = '/';
    } catch (e) {
      mostrarErro('Sem conexão com o servidor. Verifique a internet e tente novamente.');
    } finally {
      botao.disabled = false;
      botao.textContent = 'Entrar';
    }
  });
})();

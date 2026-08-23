/* ==========================================================================
   Ponte de navegação entre as views (evita import circular com o app.js).
   O app.js registra as implementações no boot; as views só chamam.
   ========================================================================== */

const registro = {
  navegar: () => {},
  recarregarObra: async () => {},
  recarregarSemana: async () => {},
  renderizarAtual: async () => {},
};

export function registrarNavegacao(implementacoes) {
  Object.assign(registro, implementacoes);
}

export const navegar = (aba, opcoes) => registro.navegar(aba, opcoes);
export const recarregarObra = (...args) => registro.recarregarObra(...args);
export const recarregarSemana = (...args) => registro.recarregarSemana(...args);
export const renderizarAtual = (...args) => registro.renderizarAtual(...args);

/* ==========================================================================
   Estado da aplicação em memória + preferências persistidas no aparelho.
   ========================================================================== */

const CHAVE_OBRA = 'obraerp:obra';
const CHAVE_ABA = 'obraerp:aba';

export const estado = {
  versao: '',
  usuario: null,
  obras: [],
  obraId: null,
  obra: null,
  semanas: [],
  semanaId: null,
  semana: null,
  dias: [],
  diaAtivo: null,        // objeto do dia selecionado no diário
  funcionarios: [],
  aba: 'home',
  mostrarArquivadas: false,
};

export const podeLancar = () => !!estado.usuario?.pode_lancar;
export const podeFinanceiro = () => !!estado.usuario?.pode_financeiro;
export const podeAdministrar = () => !!estado.usuario?.pode_administrar;

export function salvarPreferencia(chave, valor) {
  try { localStorage.setItem(chave, String(valor)); } catch (_) { /* modo privado */ }
}
export function lerPreferencia(chave) {
  try { return localStorage.getItem(chave); } catch (_) { return null; }
}

export const salvarObraPreferida = (id) => salvarPreferencia(CHAVE_OBRA, id);
export const obraPreferida = () => Number(lerPreferencia(CHAVE_OBRA)) || null;
export const salvarAbaPreferida = (aba) => salvarPreferencia(CHAVE_ABA, aba);
export const abaPreferida = () => lerPreferencia(CHAVE_ABA) || 'home';

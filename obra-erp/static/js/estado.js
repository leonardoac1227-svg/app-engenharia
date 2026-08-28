/* ==========================================================================
   Estado da aplicação em memória + preferências persistidas no aparelho.
   ========================================================================== */

const CHAVE_OBRA = 'obraerp:obra';
const CHAVE_ABA = 'obraerp:aba';
const CHAVE_RASCUNHO = 'obraerp:rascunho:';

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
  etapas: [],
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

/** Rascunho local do RDO: protege contra perda de texto quando o sinal cai
 *  no canteiro antes de o "Salvar" chegar ao servidor. */
export function salvarRascunho(diaId, dados) {
  try { localStorage.setItem(CHAVE_RASCUNHO + diaId, JSON.stringify({ ...dados, em: Date.now() })); } catch (_) {}
}
export function lerRascunho(diaId) {
  try {
    const bruto = localStorage.getItem(CHAVE_RASCUNHO + diaId);
    return bruto ? JSON.parse(bruto) : null;
  } catch (_) { return null; }
}
export function limparRascunho(diaId) {
  try { localStorage.removeItem(CHAVE_RASCUNHO + diaId); } catch (_) {}
}

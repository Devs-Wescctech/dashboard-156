from flask import Flask, jsonify
import requests
from datetime import datetime, timedelta

app = Flask(__name__)

# =========================
# CONFIGURAÇÕES FIXAS (CANAL ÚNICO + SETOR)
# =========================

API_BASE = "https://api.wescctech.com.br/core/v2/api"

CHANNEL_SLUG = "pmpa_156"
CHANNEL_NAME = "PMPA 156"
CHANNEL_TOKEN = "65b969dfbf563b1cfdd22917"  # token do canal PMPA 156

SECTOR_ID = "61bca489e5a3cfe9da65f0ad"

STATUS_AGUARDANDO = 1
STATUS_MANUAL = 2
STATUS_FINALIZADO = 3
TYPECHAT_PADRAO = 2


# =========================
# FUNÇÕES AUXILIARES
# =========================

def get_headers():
    return {
        "access-token": CHANNEL_TOKEN,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _parse_count_response(resp):
    body_text = (resp.text or "").strip()
    if body_text.isdigit():
        return int(body_text), None

    try:
        data = resp.json()
    except Exception:
        return None, "Retorno não JSON"

    if isinstance(data, dict):
        for key in ("result", "count", "total", "quantity", "amount"):
            if key in data and isinstance(data[key], (int, float)):
                return int(data[key]), None

    return None, f"Não foi possível identificar total: {data}"


def chama_chats_count(status, session, headers):
    """
    POST /chats/count com filtro por setor (sectorId)
    """
    url = f"{API_BASE}/chats/count"
    payload = {
        "status": status,
        "typeChat": TYPECHAT_PADRAO,
        "sectorId": SECTOR_ID,
    }

    try:
        resp = session.post(url, headers=headers, json=payload, timeout=15)
    except Exception as e:
        return None, f"Erro de conexão com /chats/count (status={status}): {e}"

    if not resp.ok:
        return None, f"HTTP {resp.status_code} em /chats/count (status={status}) - {resp.text}"

    return _parse_count_response(resp)


# ====== ADICIONADO: filtros de data (hoje) + count finalizados ======

def get_today_range_utc():
    """
    Início e fim do dia (00:00:00 até 23:59:59.999) em UTC,
    considerando horário local America/Sao_Paulo (UTC-3).
    """
    now_local = datetime.now()
    start_local = datetime(now_local.year, now_local.month, now_local.day, 0, 0, 0)
    end_local = datetime(now_local.year, now_local.month, now_local.day, 23, 59, 59, 999000)

    offset = timedelta(hours=3)  # local + 3 = UTC
    start_utc = start_local + offset
    end_utc = end_local + offset

    return start_utc.isoformat() + "Z", end_utc.isoformat() + "Z"


def build_date_filters():
    start_iso, end_iso = get_today_range_utc()
    return {
        "dateFilters": {
            "byStartDate": {
                "start": start_iso,
                "finish": end_iso
            }
        }
    }


def chama_chats_count_finalizados_hoje(session, headers):
    """
    POST /chats/count (FINALIZADOS) com filtro por setor e por data (hoje)
    Aumenta timeout porque pode demorar.
    """
    url = f"{API_BASE}/chats/count"
    payload = {
        "status": STATUS_FINALIZADO,
        "typeChat": TYPECHAT_PADRAO,
        "sectorId": SECTOR_ID,
    }
    payload.update(build_date_filters())

    try:
        resp = session.post(url, headers=headers, json=payload, timeout=90)
    except Exception as e:
        return None, f"Erro de conexão com /chats/count (finalizados hoje): {e}"

    if not resp.ok:
        return None, f"HTTP {resp.status_code} em /chats/count (finalizados hoje) - {resp.text}"

    return _parse_count_response(resp)

# ====== FIM DO ADICIONADO ======


def chama_chats_list_manual(session, headers):
    """
    POST /chats/list para listar TODOS os chats MANUAL do setor
    (com paginação via hasNext)
    """
    url = f"{API_BASE}/chats/list"
    page = 1
    todos = []
    avisos = []

    while True:
        payload = {
            "page": page,
            "status": STATUS_MANUAL,     # status=2 (MANUAL)
            "typeChat": TYPECHAT_PADRAO, # typeChat=2
            "sectorId": SECTOR_ID
        }

        try:
            resp = session.post(url, headers=headers, json=payload, timeout=20)
        except Exception as e:
            return None, [f"Erro de conexão com /chats/list (manual): {e}"]

        if not resp.ok:
            return None, [f"HTTP {resp.status_code} em /chats/list (manual) - {resp.text}"]

        try:
            data = resp.json()
        except Exception as e:
            return None, [f"Erro ao decodificar JSON de /chats/list (manual): {e} - corpo: {resp.text}"]

        chats = data.get("chats") or []
        if not isinstance(chats, list):
            return None, [f"Estrutura inesperada em /chats/list (manual): {data}"]

        todos.extend(chats)

        has_next = bool(data.get("hasNext"))
        if not has_next:
            break

        page += 1

        # proteção anti-loop infinito
        if page > 200:
            avisos.append("Interrompido: muitas páginas em /chats/list (manual) (possível loop).")
            break

    return todos, avisos


def agrupar_usuarios_por_chats(chats_manual):
    """
    A partir do retorno do /chats/list (manual), monta lista de usuários
    e quantidade de atendimentos (contando currentUser.id).
    """
    contagem = {}
    nomes = {}
    sem_usuario = 0

    for c in chats_manual or []:
        cu = c.get("currentUser") or {}
        uid = cu.get("id")
        uname = cu.get("name")

        if not uid:
            sem_usuario += 1
            continue

        contagem[uid] = contagem.get(uid, 0) + 1
        if uname and uid not in nomes:
            nomes[uid] = uname

    usuarios = []
    for uid, qtd in contagem.items():
        usuarios.append({
            "id": uid,
            "name": nomes.get(uid, "Sem nome"),
            "atendimentosEmAndamento": int(qtd)  # aqui = qtd de MANUAL
        })

    # ordena: maior qtd primeiro, depois nome
    usuarios.sort(key=lambda x: (-x["atendimentosEmAndamento"], x["name"]))

    return usuarios, sem_usuario


def build_resumo(headers):
    avisos = []
    hoje_str = datetime.now().date().isoformat()

    session = requests.Session()

    # 1) Contagens por status (mantém apenas AGUARDANDO e MANUAL)
    aguardando, err_aguard = chama_chats_count(STATUS_AGUARDANDO, session, headers)
    if err_aguard:
        avisos.append(f"aguardando: {err_aguard}")

    manual_total, err_manual = chama_chats_count(STATUS_MANUAL, session, headers)
    if err_manual:
        avisos.append(f"manual: {err_manual}")

    # 2) Lista de chats MANUAL para montar usuários + qtd (substitui /users)
    chats_manual, avisos_list = chama_chats_list_manual(session, headers)
    if avisos_list:
        avisos.extend(avisos_list)

    usuarios = []
    manual_sem_usuario = 0
    if chats_manual is not None:
        usuarios, manual_sem_usuario = agrupar_usuarios_por_chats(chats_manual)

    resposta = {
        "canal": {"slug": CHANNEL_SLUG, "nome": CHANNEL_NAME},
        "setor": {"id": SECTOR_ID, "nome": "PRINCIPAL"},
        "dataReferencia": hoje_str,

        # contagens por status
        "clientes": {
            "aguardando": aguardando,
            "manual": manual_total,
        },

        # usuários derivados do /chats/list (manual)
        "usuarios": usuarios,
        "manualSemUsuario": manual_sem_usuario,
        "totalUsuariosComManual": len(usuarios),
    }

    if avisos:
        resposta["avisos"] = avisos

    return resposta


# =========================
# ENDPOINTS
# =========================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "API dashboard-156 rodando",
        "canal": {"slug": CHANNEL_SLUG, "nome": CHANNEL_NAME},
        "setor": {"id": SECTOR_ID, "nome": "PRINCIPAL"},
        "endpoints": ["/resumo-hoje", "/finalizados", "/healthz"]
    })


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"ok": True})


@app.route("/resumo-hoje", methods=["GET"])
def resumo_hoje():
    headers = get_headers()
    return jsonify(build_resumo(headers)), 200


# ====== ADICIONADO: rota /finalizados ======

@app.route("/finalizados", methods=["GET"])
def finalizados():
    headers = get_headers()
    session = requests.Session()

    finalizado, err_final = chama_chats_count_finalizados_hoje(session, headers)

    resp = {
        "canal": {"slug": CHANNEL_SLUG, "nome": CHANNEL_NAME},
        "setor": {"id": SECTOR_ID, "nome": "PRINCIPAL"},
        "dataReferencia": datetime.now().date().isoformat(),
        "clientes": {
            "finalizado": finalizado
        }
    }

    if err_final:
        resp["avisos"] = [err_final]

    return jsonify(resp), 200

# ====== FIM DO ADICIONADO ======

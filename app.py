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

# SETOR PRINCIPAL (FILTRO)
SECTOR_ID = "61bca489e5a3cfe9da65f0a4"

STATUS_AUTOMATICO = 0
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


def chama_users(headers):
    url = f"{API_BASE}/users"

    try:
        resp = requests.get(url, headers=headers, timeout=10)
    except Exception as e:
        return None, None, f"Erro de conexão com /users: {e}"

    if not resp.ok:
        return None, None, f"Erro ao chamar /users: {resp.status_code} - {resp.text}"

    try:
        data = resp.json()
    except Exception as e:
        return None, None, f"Erro ao decodificar JSON de /users: {e} - corpo: {resp.text}"

    if isinstance(data, list):
        usuarios_brutos = data
    elif isinstance(data, dict) and isinstance(data.get("data"), list):
        usuarios_brutos = data["data"]
    else:
        return None, None, f"Estrutura inesperada em /users: {data}"

    usuarios_simplificados = []
    total_online = 0

    for u in usuarios_brutos:
        user_id = u.get("id")
        nome = u.get("name")
        status = u.get("status")

        usuarios_simplificados.append(
            {
                "id": user_id,
                "name": nome,
                "status": status,
                "atendimentosEmAndamento": None
            }
        )
        if isinstance(status, str) and status.upper() == "ONLINE":
            total_online += 1

    return usuarios_simplificados, total_online, None


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


def chama_chats_count(status, headers, usar_filtro_data=False):
    """
    POST /chats/count (GLOBAL) com filtro por setor (sectorId)
    """
    url = f"{API_BASE}/chats/count"
    payload = {
        "status": status,
        "typeChat": TYPECHAT_PADRAO,
        "sectorId": SECTOR_ID,  # <<< FILTRO PRINCIPAL
    }

    if usar_filtro_data:
        payload.update(build_date_filters())

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        return None, f"Erro de conexão com /chats/count (status={status}): {e}"

    if not resp.ok:
        return None, f"HTTP {resp.status_code} em /chats/count (status={status}) - {resp.text}"

    return _parse_count_response(resp)


def chama_chats_manual_por_usuario(user_id, headers):
    """
    POST /chats/count por usuário com filtro por setor (sectorId)
    """
    url = f"{API_BASE}/chats/count"
    payload = {
        "status": STATUS_MANUAL,
        "typeChat": TYPECHAT_PADRAO,
        "userId": user_id,
        "sectorId": SECTOR_ID,  # <<< FILTRO PRINCIPAL
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        return None, f"Erro de conexão com /chats/count (manual por usuário {user_id}): {e}"

    if not resp.ok:
        return None, f"HTTP {resp.status_code} em /chats/count (manual por usuário {user_id}) - {resp.text}"

    return _parse_count_response(resp)


def build_resumo(headers):
    avisos = []
    hoje_str = datetime.now().date().isoformat()

    usuarios, usuarios_online, err_users = chama_users(headers)
    if err_users:
        avisos.append(err_users)

    automatico, err_auto = chama_chats_count(STATUS_AUTOMATICO, headers, usar_filtro_data=False)
    if err_auto:
        avisos.append(f"automatico: {err_auto}")

    aguardando, err_aguard = chama_chats_count(STATUS_AGUARDANDO, headers, usar_filtro_data=False)
    if err_aguard:
        avisos.append(f"aguardando: {err_aguard}")

    manual, err_manual = chama_chats_count(STATUS_MANUAL, headers, usar_filtro_data=False)
    if err_manual:
        avisos.append(f"manual: {err_manual}")

    finalizado, err_final = chama_chats_count(STATUS_FINALIZADO, headers, usar_filtro_data=True)
    if err_final:
        avisos.append(f"finalizado: {err_final}")

    if usuarios:
        for u in usuarios:
            uid = u.get("id")
            st = (u.get("status") or "").upper()

            if not uid:
                u["atendimentosEmAndamento"] = None
                continue

            if st != "ONLINE":
                u["atendimentosEmAndamento"] = 0
                continue

            qtd, err_user = chama_chats_manual_por_usuario(uid, headers)
            if err_user:
                avisos.append(f"usuario {u.get('name')} ({uid}): {err_user}")
                u["atendimentosEmAndamento"] = None
            else:
                u["atendimentosEmAndamento"] = qtd

    resposta = {
        "canal": {"slug": CHANNEL_SLUG, "nome": CHANNEL_NAME},
        "setor": {"id": SECTOR_ID, "nome": "PRINCIPAL"},
        "dataReferencia": hoje_str,
        "usuariosOnline": usuarios_online,
        "usuarios": usuarios,
        "clientes": {
            "automatico": automatico,
            "aguardando": aguardando,
            "manual": manual,
            "finalizado": finalizado,
        },
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
        "endpoints": ["/resumo-hoje", "/healthz"]
    })


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"ok": True})


@app.route("/resumo-hoje", methods=["GET"])
def resumo_hoje():
    headers = get_headers()
    return jsonify(build_resumo(headers)), 200

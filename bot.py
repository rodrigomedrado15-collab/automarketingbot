import os
import json
import logging
import requests
import time
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── CONFIGURAÇÕES ───────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
REPLICATE_TOKEN = os.getenv("REPLICATE_TOKEN")
GROQ_TOKEN = os.getenv("GROQ_TOKEN")
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "seubot")

PLANOS = {
    "teste":   {"nome": "Teste",   "preco": 4.99,  "limite": 3},
    "starter": {"nome": "Starter", "preco": 19.90, "limite": 50},
    "pro":     {"nome": "Pro",     "preco": 47.90, "limite": 150},
}

# Exemplo fixo de anúncio
EXEMPLO_IMAGEM = "https://images.unsplash.com/photo-1583121274602-3e2820c69888?w=800"
EXEMPLO_TEXTO = (
    "🏷️ *Honda Civic 2023 — Oportunidade Única!*\n\n"
    "📝 Sedan completo com apenas 12.000 km rodados, único dono e revisões em dia. "
    "Interior impecável, multimídia original. Financiamento facilitado em até 60x!\n\n"
    "👉 *Fale agora e garanta o seu!*\n\n"
    "━━━━━━━━━━━━━━━━\n"
    "⬆️ Exemplo do que o bot cria para você em segundos!"
)

DB_FILE = "usuarios.json"

# ─── ESTADOS ─────────────────────────────────────────────────
(
    MENU_PRINCIPAL, ESCOLHER_VEICULO,
    INFORMAR_MARCA, INFORMAR_MODELO, INFORMAR_ANO,
    INFORMAR_PRECO, INFORMAR_COR, INFORMAR_DESTAQUE,
    CONFIRMAR_GERACAO, ESCOLHER_PLANO, AGUARDAR_PAGAMENTO,
) = range(11)


# ─── BANCO DE DADOS ───────────────────────────────────────────
def carregar_db():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, "r") as f:
        return json.load(f)

def salvar_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)

def obter_usuario(user_id: str):
    db = carregar_db()
    mes_atual = datetime.now().strftime("%Y-%m")
    if user_id not in db:
        db[user_id] = {"plano": None, "criacoes_mes": 0, "mes_atual": mes_atual}
        salvar_db(db)
    usuario = db[user_id]
    if usuario.get("mes_atual") != mes_atual:
        usuario["criacoes_mes"] = 0
        usuario["mes_atual"] = mes_atual
        db[user_id] = usuario
        salvar_db(db)
    return usuario

def atualizar_usuario(user_id: str, dados: dict):
    db = carregar_db()
    if user_id not in db:
        db[user_id] = {}
    db[user_id].update(dados)
    salvar_db(db)

def pode_criar(user_id: str):
    u = obter_usuario(user_id)
    if not u.get("plano"):
        return False, "sem_plano"
    if u.get("criacoes_mes", 0) >= PLANOS[u["plano"]]["limite"]:
        return False, "limite_atingido"
    return True, u.get("criacoes_mes", 0)

def incrementar_criacao(user_id: str):
    u = obter_usuario(user_id)
    atualizar_usuario(user_id, {"criacoes_mes": u.get("criacoes_mes", 0) + 1})


# ─── GERAÇÃO DE TEXTO ─────────────────────────────────────────
def gerar_texto(dados: dict) -> dict:
    prompt = f"""Você é especialista em marketing automotivo brasileiro.
Crie um anúncio para:
- Tipo: {dados['tipo']}
- Veículo: {dados['marca']} {dados['modelo']} {dados['ano']}
- Preço: R$ {dados['preco']}
- Cor: {dados['cor']}
- Destaque: {dados['destaque']}

Responda APENAS em JSON com:
- titulo: título chamativo (máx 10 palavras)
- copy: descrição persuasiva (máx 3 frases)
- cta: chamada para ação (máx 6 palavras)

Apenas o JSON puro, sem markdown."""

    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_TOKEN}"},
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 400,
            "temperature": 0.7,
        }
    )
    content = r.json()["choices"][0]["message"]["content"]
    content = content.replace("```json", "").replace("```", "").strip()
    return json.loads(content)


# ─── GERAÇÃO DE IMAGEM ────────────────────────────────────────
def gerar_imagem(dados: dict) -> str:
    prompt = (
        f"Professional car dealership advertisement banner, "
        f"{dados['ano']} {dados['marca']} {dados['modelo']}, "
        f"color {dados['cor']}, {dados['tipo'].lower()}, "
        f"ultra realistic showroom photo, dramatic lighting, "
        f"clean gradient background, modern marketing layout, 4k quality"
    )
    r = requests.post(
        "https://api.replicate.com/v1/models/black-forest-labs/flux-schnell/predictions",
        headers={"Authorization": f"Token {REPLICATE_TOKEN}", "Content-Type": "application/json"},
        json={"input": {"prompt": prompt, "num_outputs": 1}}
    )
    pid = r.json()["id"]
    for _ in range(30):
        time.sleep(2)
        res = requests.get(
            f"https://api.replicate.com/v1/predictions/{pid}",
            headers={"Authorization": f"Token {REPLICATE_TOKEN}"}
        ).json()
        if res["status"] == "succeeded":
            return res["output"][0]
        if res["status"] == "failed":
            raise Exception("Falha na geração de imagem")
    raise Exception("Timeout")


# ─── MERCADO PAGO ─────────────────────────────────────────────
def criar_link_pagamento(user_id: str, plano: str) -> str:
    p = PLANOS[plano]
    r = requests.post(
        "https://api.mercadopago.com/checkout/preferences",
        headers={"Authorization": f"Bearer {MP_ACCESS_TOKEN}", "Content-Type": "application/json"},
        json={
            "items": [{"title": f"AutoMarkBot - Plano {p['nome']}", "quantity": 1, "unit_price": p["preco"], "currency_id": "BRL"}],
            "external_reference": f"{user_id}|{plano}",
            "back_urls": {"success": f"https://t.me/{BOT_USERNAME}", "failure": f"https://t.me/{BOT_USERNAME}"},
            "auto_return": "approved",
        }
    )
    return r.json()["init_point"]


# ─── HANDLERS ────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    nome = update.effective_user.first_name
    u = obter_usuario(user_id)

    msg = (
        f"👋 Olá, {nome}! Bem-vindo ao *AutoMarkBot*!\n\n"
        "🚗 Crie anúncios profissionais para *carros, motos e caminhões* em segundos!\n\n"
        "Cada criação inclui:\n"
        "🖼️ Banner gerado por IA\n"
        "✍️ Título do anúncio\n"
        "📝 Descrição persuasiva\n"
        "👉 Chamada para ação (CTA)\n\n"
    )

    if not u.get("plano"):
        msg += "Para começar, veja um exemplo ou escolha um plano 👇"
        kb = [
            [InlineKeyboardButton("👀 Ver exemplo de anúncio", callback_data="ver_exemplo")],
            [InlineKeyboardButton("📦 Ver Planos", callback_data="ver_planos")],
        ]
    else:
        plano = u["plano"]
        criações = u.get("criacoes_mes", 0)
        limite = PLANOS[plano]["limite"]
        msg += f"✅ Plano: *{PLANOS[plano]['nome']}* | Criações: *{criações}/{limite}*"
        kb = [
            [InlineKeyboardButton("🚀 Criar Anúncio", callback_data="criar_anuncio")],
            [InlineKeyboardButton("📊 Meu Plano", callback_data="meu_plano")],
        ]

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    return MENU_PRINCIPAL


async def ver_exemplo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = [
        [InlineKeyboardButton("📦 Ver Planos", callback_data="ver_planos")],
    ]
    await context.bot.send_photo(
        chat_id=query.message.chat_id,
        photo=EXEMPLO_IMAGEM,
        caption=EXEMPLO_TEXTO,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    await query.delete_message()
    return MENU_PRINCIPAL


async def ver_planos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    texto = (
        "📋 *Escolha seu plano:*\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "🆓 *Teste — R$4,99*\n"
        "• 3 criações para experimentar\n\n"
        "📦 *Starter — R$19,90/mês*\n"
        "• 50 criações por mês\n\n"
        "⭐ *Pro — R$47,90/mês*\n"
        "• 150 criações por mês\n"
        "━━━━━━━━━━━━━━━━"
    )
    kb = [
        [InlineKeyboardButton("🆓 Experimentar — R$4,99", callback_data="assinar_teste")],
        [InlineKeyboardButton("📦 Assinar Starter — R$19,90", callback_data="assinar_starter")],
        [InlineKeyboardButton("⭐ Assinar Pro — R$47,90", callback_data="assinar_pro")],
    ]
    await query.edit_message_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    return ESCOLHER_PLANO


async def assinar_plano(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    plano = query.data.replace("assinar_", "")
    await query.edit_message_text("⏳ Gerando link de pagamento...")
    try:
        link = criar_link_pagamento(user_id, plano)
        kb = [
            [InlineKeyboardButton("💳 Pagar agora", url=link)],
            [InlineKeyboardButton("✅ Já paguei", callback_data=f"confirmar_{plano}")],
        ]
        await query.edit_message_text(
            f"🔗 Clique para pagar o plano *{PLANOS[plano]['nome']}*.\n\nApós pagar clique em *Já paguei*.",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Erro: {e}")
    return AGUARDAR_PAGAMENTO


async def confirmar_pagamento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    plano = query.data.replace("confirmar_", "")
    atualizar_usuario(user_id, {"plano": plano, "criacoes_mes": 0})
    kb = [[InlineKeyboardButton("🚀 Criar meu primeiro anúncio!", callback_data="criar_anuncio")]]
    await query.edit_message_text(
        f"✅ Plano *{PLANOS[plano]['nome']}* ativado! Bora criar anúncios! 🎉",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
    )
    return MENU_PRINCIPAL


async def criar_anuncio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    pode, motivo = pode_criar(user_id)
    if not pode:
        if motivo == "sem_plano":
            kb = [[InlineKeyboardButton("📦 Ver Planos", callback_data="ver_planos")]]
            await query.edit_message_text("❌ Você não tem plano ativo.", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query.edit_message_text("⚠️ Limite mensal atingido. Aguarde o próximo mês ou faça upgrade.")
        return MENU_PRINCIPAL

    kb = [
        [InlineKeyboardButton("🚗 Carro", callback_data="veiculo_Carro")],
        [InlineKeyboardButton("🏍️ Moto", callback_data="veiculo_Moto")],
        [InlineKeyboardButton("🚛 Caminhão", callback_data="veiculo_Caminhão")],
    ]
    await query.edit_message_text("Qual tipo de veículo?", reply_markup=InlineKeyboardMarkup(kb))
    return ESCOLHER_VEICULO


async def escolher_veiculo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["tipo"] = query.data.replace("veiculo_", "")
    await query.edit_message_text("Qual é a *marca*? (Ex: Toyota, Honda, Mercedes)", parse_mode="Markdown")
    return INFORMAR_MARCA

async def informar_marca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["marca"] = update.message.text.strip()
    await update.message.reply_text("Qual é o *modelo*? (Ex: Corolla, Civic, Actros)", parse_mode="Markdown")
    return INFORMAR_MODELO

async def informar_modelo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["modelo"] = update.message.text.strip()
    await update.message.reply_text("Qual é o *ano*? (Ex: 2023)", parse_mode="Markdown")
    return INFORMAR_ANO

async def informar_ano(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ano"] = update.message.text.strip()
    await update.message.reply_text("Qual é o *preço*? (só números, Ex: 85000)", parse_mode="Markdown")
    return INFORMAR_PRECO

async def informar_preco(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["preco"] = update.message.text.strip()
    await update.message.reply_text("Qual é a *cor*?", parse_mode="Markdown")
    return INFORMAR_COR

async def informar_cor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cor"] = update.message.text.strip()
    await update.message.reply_text(
        "Qual o *principal destaque*?\n(Ex: único dono, revisado, financiamento facilitado...)",
        parse_mode="Markdown"
    )
    return INFORMAR_DESTAQUE

async def informar_destaque(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["destaque"] = update.message.text.strip()
    d = context.user_data
    resumo = (
        f"📋 *Confirme os dados:*\n\n"
        f"🚗 {d['tipo']}: {d['marca']} {d['modelo']} {d['ano']}\n"
        f"💰 Preço: R$ {d['preco']}\n"
        f"🎨 Cor: {d['cor']}\n"
        f"⭐ Destaque: {d['destaque']}"
    )
    kb = [
        [InlineKeyboardButton("✅ Gerar anúncio!", callback_data="gerar")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar")],
    ]
    await update.message.reply_text(resumo, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    return CONFIRMAR_GERACAO


async def confirmar_geracao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    await query.edit_message_text("⏳ Gerando seu anúncio... Aguarde até 30 segundos!")

    try:
        dados = context.user_data
        texto = gerar_texto(dados)
        imagem_url = gerar_imagem(dados)
        incrementar_criacao(user_id)

        u = obter_usuario(user_id)
        criações = u["criacoes_mes"]
        limite = PLANOS[u["plano"]]["limite"]

        caption = (
            f"🏷️ *{texto['titulo']}*\n\n"
            f"📝 {texto['copy']}\n\n"
            f"👉 *{texto['cta']}*\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📊 Criações este mês: {criações}/{limite}"
        )
        kb = [[InlineKeyboardButton("🔄 Criar outro", callback_data="criar_anuncio")]]
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=imagem_url,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        await query.delete_message()

    except Exception as e:
        logger.error(f"Erro: {e}")
        kb = [[InlineKeyboardButton("🔄 Tentar novamente", callback_data="criar_anuncio")]]
        await query.edit_message_text("❌ Erro ao gerar. Tente novamente.", reply_markup=InlineKeyboardMarkup(kb))

    return MENU_PRINCIPAL


async def meu_plano(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    u = obter_usuario(user_id)

    if not u.get("plano"):
        kb = [[InlineKeyboardButton("📦 Ver Planos", callback_data="ver_planos")]]
        await query.edit_message_text("Sem plano ativo.", reply_markup=InlineKeyboardMarkup(kb))
        return MENU_PRINCIPAL

    plano = u["plano"]
    criações = u.get("criacoes_mes", 0)
    limite = PLANOS[plano]["limite"]
    texto = (
        f"📊 *Seu Plano*\n\n"
        f"✅ *{PLANOS[plano]['nome']}* — R${PLANOS[plano]['preco']}/mês\n"
        f"🖼️ Usadas: *{criações}/{limite}*\n"
        f"🔄 Restantes: *{limite - criações}*"
    )
    kb = [[InlineKeyboardButton("🚀 Criar Anúncio", callback_data="criar_anuncio")]]
    if plano == "starter":
        kb.append([InlineKeyboardButton("⭐ Upgrade para Pro", callback_data="assinar_pro")])
    await query.edit_message_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    return MENU_PRINCIPAL


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    kb = [[InlineKeyboardButton("🚀 Criar Anúncio", callback_data="criar_anuncio")]]
    await query.edit_message_text("Cancelado. O que deseja fazer?", reply_markup=InlineKeyboardMarkup(kb))
    return MENU_PRINCIPAL


# ─── MAIN ─────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU_PRINCIPAL: [
                CallbackQueryHandler(ver_exemplo, pattern="^ver_exemplo$"),
                CallbackQueryHandler(ver_planos, pattern="^ver_planos$"),
                CallbackQueryHandler(criar_anuncio, pattern="^criar_anuncio$"),
                CallbackQueryHandler(meu_plano, pattern="^meu_plano$"),
            ],
            ESCOLHER_PLANO: [CallbackQueryHandler(assinar_plano, pattern="^assinar_")],
            AGUARDAR_PAGAMENTO: [CallbackQueryHandler(confirmar_pagamento, pattern="^confirmar_")],
            ESCOLHER_VEICULO: [CallbackQueryHandler(escolher_veiculo, pattern="^veiculo_")],
            INFORMAR_MARCA:    [MessageHandler(filters.TEXT & ~filters.COMMAND, informar_marca)],
            INFORMAR_MODELO:   [MessageHandler(filters.TEXT & ~filters.COMMAND, informar_modelo)],
            INFORMAR_ANO:      [MessageHandler(filters.TEXT & ~filters.COMMAND, informar_ano)],
            INFORMAR_PRECO:    [MessageHandler(filters.TEXT & ~filters.COMMAND, informar_preco)],
            INFORMAR_COR:      [MessageHandler(filters.TEXT & ~filters.COMMAND, informar_cor)],
            INFORMAR_DESTAQUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, informar_destaque)],
            CONFIRMAR_GERACAO: [
                CallbackQueryHandler(confirmar_geracao, pattern="^gerar$"),
                CallbackQueryHandler(cancelar, pattern="^cancelar$"),
            ],
        },
        fallbacks=[CommandHandler("start", start), CallbackQueryHandler(cancelar, pattern="^cancelar$")],
    )
    app.add_handler(conv)
    logger.info("✅ Bot iniciado!")
    app.run_polling()

if __name__ == "__main__":
    main()

import os
import qrcode
import io
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from supabase import create_client, Client
import requests
import logging
from datetime import datetime
import secrets

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Conversation states
WAITING_TOKEN, WAITING_PAYMENT_PROOF = range(2)

# Helper functions
def get_bot_settings():
    """Get bot settings from database"""
    result = supabase.table('bot_settings').select('*').limit(1).execute()
    if result.data:
        return result.data[0]
    return None

def get_all_admins():
    """Get all admin IDs"""
    result = supabase.table('admins').select('telegram_id').eq('is_active', True).execute()
    return [admin['telegram_id'] for admin in result.data] if result.data else []

def is_admin(user_id):
    """Check if user is admin"""
    return user_id in get_all_admins()

def get_upi_details():
    """Get active UPI details"""
    result = supabase.table('upi_config').select('*').eq('is_active', True).limit(1).execute()
    if result.data:
        return result.data[0]
    return None

def get_all_packages():
    """Get all active packages"""
    result = supabase.table('packages').select('*').eq('is_active', True).order('amount').execute()
    return result.data

def get_package_by_id(package_id):
    """Get specific package"""
    result = supabase.table('packages').select('*').eq('id', package_id).execute()
    if result.data:
        return result.data[0]
    return None

def save_user(user_id, username, first_name, last_name=None):
    """Save or update user in database"""
    data = {
        'user_id': user_id,
        'username': username,
        'first_name': first_name,
        'last_name': last_name,
        'last_interaction': datetime.utcnow().isoformat()
    }
    
    # Check if user exists
    existing = supabase.table('users').select('*').eq('user_id', user_id).execute()
    
    if existing.data:
        supabase.table('users').update(data).eq('user_id', user_id).execute()
    else:
        supabase.table('users').insert(data).execute()

async def notify_admins_new_user(context, user_id, username, first_name):
    """Notify all admins about new user"""
    admins = get_all_admins()
    message = (
        f"🆕 *New User Started Bot*\n\n"
        f"👤 Name: {first_name}\n"
        f"🆔 User ID: `{user_id}`\n"
        f"📱 Username: @{username if username else 'N/A'}\n"
        f"🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    
    for admin_id in admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=message,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

def generate_token_id():
    """Generate unique token ID"""
    return secrets.token_hex(16)

def save_token(user_id, username, package_id, transaction_id, amount):
    """Save token to database"""
    token_id = generate_token_id()
    data = {
        'token_id': token_id,
        'user_id': user_id,
        'username': username,
        'package_id': package_id,
        'transaction_id': transaction_id,
        'amount': amount,
        'status': 'active',
        'created_at': datetime.utcnow().isoformat()
    }
    result = supabase.table('tokens').insert(data).execute()
    return token_id if result.data else None

def generate_key(token_id, user_id):
    """Generate key from token"""
    result = supabase.table('tokens').select('*').eq('token_id', token_id).eq('user_id', user_id).eq('status', 'active').execute()
    
    if not result.data:
        return None
    
    token_data = result.data[0]
    package = get_package_by_id(token_data['package_id'])
    
    if not package:
        return None
    
    key = secrets.token_urlsafe(32)
    
    key_data = {
        'key': key,
        'user_id': user_id,
        'package_id': token_data['package_id'],
        'token_id': token_id,
        'validity_days': package['validity'],
        'created_at': datetime.utcnow().isoformat(),
        'status': 'active'
    }
    supabase.table('keys').insert(key_data).execute()
    supabase.table('tokens').update({'status': 'used'}).eq('token_id', token_id).execute()
    
    return key

def verify_transaction(transaction_id, expected_amount):
    """Verify transaction via API"""
    settings = get_bot_settings()
    
    if not settings or not settings.get('api_token'):
        return {'status': 'ERROR', 'message': 'API configuration not found'}
    
    api_token = settings['api_token']
    url = f"https://api.intechost.com/bharatpe/api.php?token={api_token}&txn_id={transaction_id}"
    
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        
        if data.get('status') == 'SUCCESS' and float(data.get('amount', 0)) == float(expected_amount):
            return {
                'status': 'SUCCESS',
                'amount': data.get('amount'),
                'payer': data.get('payer'),
                'app': data.get('app')
            }
        else:
            return {
                'status': 'FAILED',
                'message': f"Verification failed. Status: {data.get('status')}, Amount: {data.get('amount')}"
            }
    except Exception as e:
        logger.error(f"API error: {e}")
        return {'status': 'ERROR', 'message': str(e)}

# Bot handlers
async def check_channel_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if user is member of required channel"""
    settings = get_bot_settings()
    
    if not settings or not settings.get('force_channel'):
        return True
    
    user_id = update.effective_user.id
    channel = settings['force_channel'].strip()
    
    # Remove @ if present in channel ID (IDs should not have @)
    if channel.startswith('@-'):
        channel = channel[1:]  # Remove the @ from @-100...
    
    try:
        member = await context.bot.get_chat_member(channel, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        error_msg = str(e)
        
        if "Chat not found" in error_msg:
            logger.error(f"❌ Channel Error: '{channel}' not found!")
            logger.error("💡 Make sure bot is ADDED to the channel as ADMIN")
            logger.error("💡 For channel IDs: Use -100123456789 (WITHOUT @)")
            logger.error("💡 For usernames: Use @channelname")
            return True
        else:
            logger.error(f"Channel membership check error: {error_msg}")
            return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user = update.effective_user
    
    # Save user to database
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    # Check if first time user
    existing = supabase.table('users').select('*').eq('user_id', user.id).execute()
    if not existing.data or len(existing.data) == 0:
        # Notify admins about new user
        await notify_admins_new_user(context, user.id, user.username, user.first_name)
    
    # Check channel membership
    settings = get_bot_settings()
    
    if settings and settings.get('force_channel'):
        is_member = await check_channel_membership(update, context)
        
        if not is_member:
            channel = settings['force_channel']
            
            # Handle both username and ID formats
            if channel.startswith('@'):
                channel_username = channel.replace('@', '')
                join_url = f"https://t.me/{channel_username}"
            elif channel.startswith('-100'):
                join_url = None
            else:
                channel_username = channel
                join_url = f"https://t.me/{channel_username}"
            
            keyboard = []
            
            if join_url:
                keyboard.append([InlineKeyboardButton("📢 Join Channel", url=join_url)])
            else:
                await update.message.reply_text(
                    f"🚫 *Access Denied!*\n\n"
                    f"Please join our channel: `{channel}`\n\n"
                    f"Contact admin for channel link.",
                    parse_mode='Markdown'
                )
                return
            
            keyboard.append([InlineKeyboardButton("✅ I Joined, Verify", callback_data="verify_membership")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"🚫 *Access Denied!*\n\n"
                f"Please join our channel first: {channel}\n\n"
                f"After joining, click 'Verify' button.",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            return
    
    await show_main_menu(update, context)

async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify button callback"""
    query = update.callback_query
    await query.answer()
    
    is_member = await check_channel_membership(update, context)
    
    if not is_member:
        await query.message.reply_text(
            "❌ You are not a member yet.\n"
            "Please join the channel first, then verify."
        )
        return
    
    await query.message.reply_text("✅ Verification successful!")
    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show main menu"""
    keyboard = [
        [InlineKeyboardButton("🔑 Generate Key", callback_data="generate_key")],
        [InlineKeyboardButton("💳 Buy Package", callback_data="buy_package")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "🎯 *Welcome to Key Generator Bot!*\n\n"
        "What would you like to do?\n\n"
        "• *Generate Key* - Generate key using token\n"
        "• *Buy Package* - Purchase new package"
    )
    
    if update.callback_query:
        await update.callback_query.message.edit_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

async def generate_key_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate key callback"""
    query = update.callback_query
    await query.answer()
    
    await query.message.reply_text(
        "🔑 *Generate Key*\n\n"
        "Please enter your Token ID:",
        parse_mode='Markdown'
    )
    
    return WAITING_TOKEN

async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive token and generate key"""
    token_id = update.message.text.strip()
    user_id = update.effective_user.id
    
    key = generate_key(token_id, user_id)
    
    if key:
        await update.message.reply_text(
            f"✅ *Key Generated Successfully!*\n\n"
            f"🔐 Your Key:\n`{key}`\n\n"
            f"⚠️ Keep this key safe!",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "❌ Invalid token or token already used.\n"
            "Please check and try again."
        )
    
    return ConversationHandler.END

async def buy_package_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buy package callback"""
    query = update.callback_query
    await query.answer()
    
    packages = get_all_packages()
    
    if not packages:
        await query.message.reply_text("❌ No packages available at the moment.")
        return
    
    keyboard = []
    for pkg in packages:
        keyboard.append([InlineKeyboardButton(
            f"{pkg['plan_name']} - ₹{pkg['amount']} ({pkg['validity']} days)",
            callback_data=f"select_pkg_{pkg['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("« Back", callback_data="back_to_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(
        "💳 *Available Packages*\n\nSelect a package:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def select_package_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Package selection callback - SIMPLIFIED FLOW"""
    query = update.callback_query
    await query.answer()
    
    package_id = int(query.data.split('_')[2])
    package = get_package_by_id(package_id)
    
    if not package:
        await query.message.reply_text("❌ Package not found!")
        return
    
    context.user_data['selected_package_id'] = package_id
    
    upi_details = get_upi_details()
    
    if not upi_details:
        await query.message.reply_text("❌ Payment method not configured!")
        return
    
    # Generate QR code
    upi_string = f"upi://pay?pa={upi_details['upi_id']}&pn={upi_details['name']}&am={package['amount']}&cu=INR"
    
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(upi_string)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    bio = io.BytesIO()
    img.save(bio, 'PNG')
    bio.seek(0)
    
    # Send QR code without deep link button (Telegram doesn't support upi://)
    keyboard = [
        [InlineKeyboardButton("« Back", callback_data="buy_package")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.reply_photo(
        photo=InputFile(bio, filename='qr.png'),
        caption=(
            f"📦 *{package['plan_name']}*\n\n"
            f"💰 Amount: ₹{package['amount']}\n"
            f"⏱ Validity: {package['validity']} days\n"
            f"📝 Description: {package['description']}\n\n"
            f"💳 UPI ID: `{upi_details['upi_id']}`\n"
            f"👤 Name: {upi_details['name']}\n\n"
            f"📱 *Payment Steps:*\n"
            f"1️⃣ Scan the QR code above\n"
            f"2️⃣ Or copy UPI ID and pay manually\n"
            f"3️⃣ Complete payment in your UPI app\n\n"
            f"💡 *After Payment:*\n"
            f"📤 Send Transaction ID/UTR (instant verification)\n"
            f"📸 Or send payment screenshot (manual review)\n\n"
            f"👇 Waiting for your payment proof..."
        ),
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
    
    return WAITING_PAYMENT_PROOF

async def receive_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive either transaction ID (text) or screenshot (photo)"""
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    package_id = context.user_data.get('selected_package_id')
    
    if not package_id:
        await update.message.reply_text("❌ Error: Package not found!")
        return ConversationHandler.END
    
    package = get_package_by_id(package_id)
    
    # Check if user sent a photo (screenshot)
    if update.message.photo:
        await handle_screenshot_submission(update, context, user_id, username, package_id, package)
    # Check if user sent text (transaction ID)
    elif update.message.text:
        await handle_transaction_id_submission(update, context, user_id, username, package_id, package)
    else:
        await update.message.reply_text(
            "❌ Please send either:\n"
            "📤 Transaction ID/UTR (text)\n"
            "📸 Payment screenshot (image)"
        )
        return WAITING_PAYMENT_PROOF
    
    return ConversationHandler.END

async def handle_transaction_id_submission(update, context, user_id, username, package_id, package):
    """Handle automatic transaction ID verification"""
    txn_id = update.message.text.strip()
    
    await update.message.reply_text("⏳ Verifying transaction...")
    
    result = verify_transaction(txn_id, package['amount'])
    
    if result['status'] == 'SUCCESS':
        # Auto-generate token and key
        token_id = save_token(user_id, username, package_id, txn_id, package['amount'])
        
        # Generate key immediately
        key = secrets.token_urlsafe(32)
        key_data = {
            'key': key,
            'user_id': user_id,
            'package_id': package_id,
            'token_id': token_id,
            'validity_days': package['validity'],
            'created_at': datetime.utcnow().isoformat(),
            'status': 'active'
        }
        supabase.table('keys').insert(key_data).execute()
        supabase.table('tokens').update({'status': 'used'}).eq('token_id', token_id).execute()
        
        await update.message.reply_text(
            f"✅ *Payment Verified & Approved!*\n\n"
            f"💰 Amount: ₹{result['amount']}\n"
            f"👤 Payer: {result['payer']}\n"
            f"📱 App: {result['app']}\n\n"
            f"📦 Package: {package['plan_name']}\n"
            f"⏱ Validity: {package['validity']} days\n\n"
            f"🔐 *Your Key:*\n`{key}`\n\n"
            f"⚠️ Keep this key safe!",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"❌ *Automatic Verification Failed!*\n\n"
            f"{result.get('message', 'Unknown error')}\n\n"
            f"💡 You can send payment screenshot instead for manual review.",
            parse_mode='Markdown'
        )

async def handle_screenshot_submission(update, context, user_id, username, package_id, package):
    """Handle manual screenshot review"""
    photo = update.message.photo[-1]
    file_id = photo.file_id
    
    # Save to pending transactions
    pending_data = {
        'user_id': user_id,
        'username': username,
        'package_id': package_id,
        'screenshot_file_id': file_id,
        'status': 'pending',
        'created_at': datetime.utcnow().isoformat()
    }
    supabase.table('pending_transactions').insert(pending_data).execute()
    
    await update.message.reply_text(
        "✅ *Screenshot Received!*\n\n"
        "Your payment is under review.\n"
        "⏱ Review time: 1-2 hours\n\n"
        "You will receive your key once approved by admin.",
        parse_mode='Markdown'
    )
    
    # Notify all admins
    admins = get_all_admins()
    
    for admin_id in admins:
        try:
            keyboard = [
                [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}_{package_id}_{file_id}")],
                [InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}_{package_id}_{file_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=file_id,
                caption=(
                    f"📸 *New Payment Screenshot*\n\n"
                    f"👤 User: @{username}\n"
                    f"🆔 User ID: `{user_id}`\n"
                    f"📦 Package: {package['plan_name']}\n"
                    f"💰 Amount: ₹{package['amount']}\n"
                    f"⏱ Validity: {package['validity']} days"
                ),
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

async def back_to_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back to main menu"""
    query = update.callback_query
    await query.answer()
    await show_main_menu(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversation"""
    await update.message.reply_text("❌ Cancelled!")
    return ConversationHandler.END

def main():
    """Start bot"""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Main conversation handler
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(generate_key_callback, pattern="^generate_key$"),
            CallbackQueryHandler(select_package_callback, pattern="^select_pkg_")
        ],
        states={
            WAITING_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token)],
            WAITING_PAYMENT_PROOF: [
                MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), receive_payment_proof)
            ]
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            CallbackQueryHandler(buy_package_callback, pattern="^buy_package$"),
            CallbackQueryHandler(back_to_menu_callback, pattern="^back_to_menu$")
        ]
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(verify_callback, pattern="^verify_membership$"))
    application.add_handler(CallbackQueryHandler(buy_package_callback, pattern="^buy_package$"))
    application.add_handler(CallbackQueryHandler(back_to_menu_callback, pattern="^back_to_menu$"))
    application.add_handler(conv_handler)
    
    # Import and add admin handlers
    from admin_commands import get_admin_handlers
    for handler in get_admin_handlers():
        application.add_handler(handler)
    
    logger.info("Bot starting...")
    logger.info("=" * 60)
    logger.info("📢 CHANNEL SETUP GUIDE:")
    logger.info("=" * 60)
    logger.info("For PUBLIC channels:")
    logger.info("  1. Add bot to channel as ADMIN")
    logger.info("  2. Set channel username in bot_settings: @channelname")
    logger.info("")
    logger.info("For PRIVATE channels:")
    logger.info("  1. Add bot to channel as ADMIN")
    logger.info("  2. Get channel ID (forward message from channel to @userinfobot)")
    logger.info("  3. Set channel ID in bot_settings: -100123456789")
    logger.info("=" * 60)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

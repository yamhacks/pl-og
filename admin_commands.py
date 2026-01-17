"""
Admin Commands Module
Complete admin panel for bot management with Limited Admin support
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from supabase import create_client, Client
import os
import secrets
from datetime import datetime
import requests

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Conversation states for admin
(ADMIN_WAITING_USERNAME, ADMIN_WAITING_PACKAGE, ADMIN_WAITING_TXN,
 ADMIN_ADD_UPI_ID, ADMIN_ADD_UPI_NAME, ADMIN_ADD_ADMIN_ID, ADMIN_ADD_ADMIN_ROLE,
 ADMIN_ADD_PKG_NAME, ADMIN_ADD_PKG_DESC, ADMIN_ADD_PKG_AMOUNT, ADMIN_ADD_PKG_VALIDITY,
 ADMIN_EDIT_CHANNEL, ADMIN_EDIT_API) = range(20, 33)

def get_all_admins():
    """Get all admin IDs"""
    result = supabase.table('admins').select('telegram_id').eq('is_active', True).execute()
    return [admin['telegram_id'] for admin in result.data] if result.data else []

def is_admin(user_id):
    """Check if user is admin"""
    return user_id in get_all_admins()

def get_admin_role(user_id):
    """Get admin role - returns 'super' or 'limited'"""
    result = supabase.table('admins').select('role').eq('telegram_id', user_id).eq('is_active', True).execute()
    if result.data:
        return result.data[0].get('role', 'limited')
    return None

def is_super_admin(user_id):
    """Check if user is super admin"""
    return get_admin_role(user_id) == 'super'

def verify_transaction(transaction_id, expected_amount):
    """Verify transaction via API"""
    settings = supabase.table('bot_settings').select('*').limit(1).execute().data
    
    if not settings or not settings[0].get('api_token'):
        return {'status': 'ERROR', 'message': 'API configuration not found'}
    
    api_token = settings[0]['api_token']
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
        return {'status': 'ERROR', 'message': str(e)}

# === ADMIN PANEL ===
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main admin panel"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Unauthorized! Admin access only.")
        return
    
    role = get_admin_role(user_id)
    
    if role == 'limited':
        # Limited admin menu
        keyboard = [
            [InlineKeyboardButton("➕ Generate Token Manually", callback_data="admin_gen_token")],
            [InlineKeyboardButton("📋 View Pending Reviews", callback_data="admin_pending")],
            [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🔐 *Limited Admin Panel*\n\nSelect an option:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    else:
        # Super admin menu
        keyboard = [
            [InlineKeyboardButton("➕ Generate Token Manually", callback_data="admin_gen_token")],
            [InlineKeyboardButton("📋 View Pending Reviews", callback_data="admin_pending")],
            [InlineKeyboardButton("📦 Manage Packages", callback_data="admin_packages")],
            [InlineKeyboardButton("💳 Manage UPI", callback_data="admin_upi")],
            [InlineKeyboardButton("👥 Manage Admins", callback_data="admin_admins")],
            [InlineKeyboardButton("⚙️ Bot Settings", callback_data="admin_settings")],
            [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🔑 *Super Admin Panel*\n\nSelect an option:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

# === MANUAL TOKEN GENERATION (WITH TRANSACTION VERIFICATION) ===
async def admin_generate_token_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start manual token generation"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(update.effective_user.id):
        await query.message.reply_text("❌ Unauthorized!")
        return ConversationHandler.END
    
    await query.message.reply_text(
        "👤 *Manual Token Generation*\n\n"
        "Enter username (with or without @):",
        parse_mode='Markdown'
    )
    
    return ADMIN_WAITING_USERNAME

async def admin_receive_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive username"""
    username = update.message.text.strip().replace('@', '')
    context.user_data['admin_target_username'] = username
    
    packages = supabase.table('packages').select('*').eq('is_active', True).execute().data
    
    if not packages:
        await update.message.reply_text("❌ No packages available!")
        return ConversationHandler.END
    
    keyboard = []
    for pkg in packages:
        keyboard.append([InlineKeyboardButton(
            f"{pkg['plan_name']} - ₹{pkg['amount']}",
            callback_data=f"admin_pkg_{pkg['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("« Cancel", callback_data="admin_cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✅ Username: @{username}\n\n📦 Select Package:",
        reply_markup=reply_markup
    )
    
    return ADMIN_WAITING_PACKAGE

async def admin_select_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Select package"""
    query = update.callback_query
    await query.answer()
    
    package_id = int(query.data.split('_')[2])
    context.user_data['admin_package_id'] = package_id
    
    package = supabase.table('packages').select('*').eq('id', package_id).execute().data[0]
    
    await query.message.reply_text(
        f"📦 Package: {package['plan_name']}\n"
        f"💰 Amount: ₹{package['amount']}\n\n"
        f"🔢 Enter Transaction ID for verification:",
        parse_mode='Markdown'
    )
    
    return ADMIN_WAITING_TXN

async def admin_receive_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive transaction and verify before generating token"""
    txn_id = update.message.text.strip()
    username = context.user_data.get('admin_target_username')
    package_id = context.user_data.get('admin_package_id')
    
    package = supabase.table('packages').select('*').eq('id', package_id).execute().data[0]
    
    await update.message.reply_text("⏳ Verifying transaction...")
    
    # Verify transaction
    result = verify_transaction(txn_id, package['amount'])
    
    if result['status'] == 'SUCCESS':
        # Generate token after successful verification
        token_id = secrets.token_hex(16)
        
        data = {
            'token_id': token_id,
            'user_id': 0,
            'username': username,
            'package_id': package_id,
            'transaction_id': txn_id,
            'amount': package['amount'],
            'status': 'active',
            'created_at': datetime.utcnow().isoformat()
        }
        
        supabase.table('tokens').insert(data).execute()
        
        await update.message.reply_text(
            f"✅ *Token Generated Successfully!*\n\n"
            f"👤 Username: @{username}\n"
            f"📦 Package: {package['plan_name']}\n"
            f"💰 Amount: ₹{package['amount']}\n"
            f"🔢 Transaction ID: {txn_id}\n\n"
            f"✅ Verified Details:\n"
            f"💵 Paid: ₹{result['amount']}\n"
            f"👤 Payer: {result['payer']}\n"
            f"📱 App: {result['app']}\n\n"
            f"🎟 *Token ID:*\n`{token_id}`\n\n"
            f"Share this token with the user.",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"❌ *Transaction Verification Failed!*\n\n"
            f"{result.get('message', 'Unknown error')}\n\n"
            f"Token NOT generated. Please verify the transaction ID.",
            parse_mode='Markdown'
        )
    
    return ConversationHandler.END

# === PENDING REVIEWS ===
async def admin_view_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View pending reviews"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(update.effective_user.id):
        await query.message.reply_text("❌ Unauthorized!")
        return
    
    pending = supabase.table('pending_transactions').select('*').eq('status', 'pending').execute().data
    
    if not pending:
        await query.message.reply_text("✅ No pending reviews!")
        return
    
    await query.message.reply_text(f"📋 *Pending Reviews: {len(pending)}*\n\nFetching...", parse_mode='Markdown')
    
    for transaction in pending:
        package = supabase.table('packages').select('*').eq('id', transaction['package_id']).execute().data[0]
        
        keyboard = [
            [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{transaction['id']}")],
            [InlineKeyboardButton("❌ Reject", callback_data=f"reject_{transaction['id']}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_photo(
            chat_id=update.effective_user.id,
            photo=transaction['screenshot_file_id'],
            caption=(
                f"📸 *Pending Review*\n\n"
                f"👤 Username: @{transaction['username']}\n"
                f"🆔 User ID: `{transaction['user_id']}`\n"
                f"📦 Package: {package['plan_name']}\n"
                f"💰 Amount: ₹{package['amount']}\n"
                f"📅 Submitted: {transaction['created_at']}"
            ),
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

async def admin_approve_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve screenshot"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(update.effective_user.id):
        await query.message.reply_text("❌ Unauthorized!")
        return
    
    transaction_id = int(query.data.split('_')[1])
    
    transaction = supabase.table('pending_transactions').select('*').eq('id', transaction_id).execute().data[0]
    package = supabase.table('packages').select('*').eq('id', transaction['package_id']).execute().data[0]
    
    token_id = secrets.token_hex(16)
    
    token_data = {
        'token_id': token_id,
        'user_id': transaction['user_id'],
        'username': transaction['username'],
        'package_id': transaction['package_id'],
        'transaction_id': f"SS_{transaction_id}",
        'amount': package['amount'],
        'status': 'active',
        'created_at': datetime.utcnow().isoformat()
    }
    supabase.table('tokens').insert(token_data).execute()
    
    key = secrets.token_urlsafe(32)
    key_data = {
        'key': key,
        'user_id': transaction['user_id'],
        'package_id': transaction['package_id'],
        'token_id': token_id,
        'validity_days': package['validity'],
        'status': 'active',
        'created_at': datetime.utcnow().isoformat()
    }
    supabase.table('keys').insert(key_data).execute()
    supabase.table('tokens').update({'status': 'used'}).eq('token_id', token_id).execute()
    
    supabase.table('pending_transactions').update({
        'status': 'approved',
        'reviewed_at': datetime.utcnow().isoformat(),
        'reviewed_by': update.effective_user.id
    }).eq('id', transaction_id).execute()
    
    try:
        await context.bot.send_message(
            chat_id=transaction['user_id'],
            text=(
                f"✅ *Payment Approved!*\n\n"
                f"📦 Package: {package['plan_name']}\n"
                f"⏱ Validity: {package['validity']} days\n\n"
                f"🔐 Your Key:\n`{key}`\n\n"
                f"⚠️ Keep this key safe!"
            ),
            parse_mode='Markdown'
        )
    except:
        pass
    
    await query.message.edit_caption(
        caption=query.message.caption + "\n\n✅ *APPROVED*",
        parse_mode='Markdown'
    )

async def admin_reject_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reject screenshot"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(update.effective_user.id):
        await query.message.reply_text("❌ Unauthorized!")
        return
    
    transaction_id = int(query.data.split('_')[1])
    transaction = supabase.table('pending_transactions').select('*').eq('id', transaction_id).execute().data[0]
    
    supabase.table('pending_transactions').update({
        'status': 'rejected',
        'reviewed_at': datetime.utcnow().isoformat(),
        'reviewed_by': update.effective_user.id
    }).eq('id', transaction_id).execute()
    
    try:
        await context.bot.send_message(
            chat_id=transaction['user_id'],
            text=(
                "❌ *Payment Rejected*\n\n"
                "Your payment screenshot has been rejected.\n"
                "Please contact admin for more details."
            ),
            parse_mode='Markdown'
        )
    except:
        pass
    
    await query.message.edit_caption(
        caption=query.message.caption + "\n\n❌ *REJECTED*",
        parse_mode='Markdown'
    )

# === PACKAGE MANAGEMENT (SUPER ADMIN ONLY) ===
async def admin_packages_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Packages management menu"""
    query = update.callback_query
    await query.answer()
    
    if not is_super_admin(update.effective_user.id):
        await query.message.reply_text("❌ Unauthorized! Super admin access only.")
        return
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Package", callback_data="admin_add_package")],
        [InlineKeyboardButton("📋 View Packages", callback_data="admin_view_packages")],
        [InlineKeyboardButton("« Back", callback_data="admin_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(
        "📦 *Package Management*\n\nSelect an option:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def admin_view_packages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View all packages"""
    query = update.callback_query
    await query.answer()
    
    packages = supabase.table('packages').select('*').execute().data
    
    if not packages:
        await query.message.reply_text("No packages found!")
        return
    
    text = "📦 *All Packages:*\n\n"
    for pkg in packages:
        status = "✅" if pkg['is_active'] else "❌"
        text += (
            f"{status} *{pkg['plan_name']}*\n"
            f"💰 ₹{pkg['amount']} | ⏱ {pkg['validity']} days\n"
            f"📝 {pkg['description']}\n\n"
        )
    
    keyboard = [[InlineKeyboardButton("« Back", callback_data="admin_packages")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(text, parse_mode='Markdown', reply_markup=reply_markup)

async def admin_add_package_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding package"""
    query = update.callback_query
    await query.answer()
    
    if not is_super_admin(update.effective_user.id):
        await query.message.reply_text("❌ Unauthorized!")
        return ConversationHandler.END
    
    await query.message.reply_text(
        "➕ *Add New Package*\n\nEnter package name:",
        parse_mode='Markdown'
    )
    
    return ADMIN_ADD_PKG_NAME

async def admin_add_package_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive package name"""
    context.user_data['pkg_name'] = update.message.text.strip()
    
    await update.message.reply_text("Enter package description:")
    return ADMIN_ADD_PKG_DESC

async def admin_add_package_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive package description"""
    context.user_data['pkg_desc'] = update.message.text.strip()
    
    await update.message.reply_text("Enter package amount (in ₹):")
    return ADMIN_ADD_PKG_AMOUNT

async def admin_add_package_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive package amount"""
    try:
        amount = float(update.message.text.strip())
        context.user_data['pkg_amount'] = amount
        
        await update.message.reply_text("Enter validity (in days):")
        return ADMIN_ADD_PKG_VALIDITY
    except:
        await update.message.reply_text("❌ Invalid amount! Please enter a number:")
        return ADMIN_ADD_PKG_AMOUNT

async def admin_add_package_validity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive validity and save package"""
    try:
        validity = int(update.message.text.strip())
        
        data = {
            'plan_name': context.user_data['pkg_name'],
            'description': context.user_data['pkg_desc'],
            'amount': context.user_data['pkg_amount'],
            'validity': validity,
            'is_active': True,
            'created_at': datetime.utcnow().isoformat()
        }
        
        result = supabase.table('packages').insert(data).execute()
        
        if result.data:
            await update.message.reply_text(
                f"✅ *Package Added Successfully!*\n\n"
                f"📦 Name: {data['plan_name']}\n"
                f"💰 Amount: ₹{data['amount']}\n"
                f"⏱ Validity: {data['validity']} days",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ Error adding package!")
        
        return ConversationHandler.END
    except:
        await update.message.reply_text("❌ Invalid validity! Please enter a number:")
        return ADMIN_ADD_PKG_VALIDITY

# === UPI MANAGEMENT (SUPER ADMIN ONLY) ===
async def admin_upi_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """UPI management menu"""
    query = update.callback_query
    await query.answer()
    
    if not is_super_admin(update.effective_user.id):
        await query.message.reply_text("❌ Unauthorized! Super admin access only.")
        return
    
    keyboard = [
        [InlineKeyboardButton("➕ Add UPI", callback_data="admin_add_upi")],
        [InlineKeyboardButton("📋 View UPI", callback_data="admin_view_upi")],
        [InlineKeyboardButton("« Back", callback_data="admin_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(
        "💳 *UPI Management*\n\nSelect an option:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def admin_view_upi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View UPI details"""
    query = update.callback_query
    await query.answer()
    
    upis = supabase.table('upi_config').select('*').execute().data
    
    if not upis:
        await query.message.reply_text("No UPI configured!")
        return
    
    text = "💳 *UPI Details:*\n\n"
    for upi in upis:
        status = "✅ Active" if upi['is_active'] else "❌ Inactive"
        text += (
            f"{status}\n"
            f"🆔 UPI ID: `{upi['upi_id']}`\n"
            f"👤 Name: {upi['name']}\n\n"
        )
    
    keyboard = [[InlineKeyboardButton("« Back", callback_data="admin_upi")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(text, parse_mode='Markdown', reply_markup=reply_markup)

async def admin_add_upi_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding UPI"""
    query = update.callback_query
    await query.answer()
    
    if not is_super_admin(update.effective_user.id):
        await query.message.reply_text("❌ Unauthorized!")
        return ConversationHandler.END
    
    await query.message.reply_text(
        "➕ *Add UPI Details*\n\nEnter UPI ID:",
        parse_mode='Markdown'
    )
    
    return ADMIN_ADD_UPI_ID

async def admin_add_upi_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive UPI ID"""
    context.user_data['upi_id'] = update.message.text.strip()
    
    await update.message.reply_text("Enter UPI Name:")
    return ADMIN_ADD_UPI_NAME

async def admin_add_upi_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive UPI name and save"""
    upi_name = update.message.text.strip()
    
    # Deactivate all existing UPI
    supabase.table('upi_config').update({'is_active': False}).neq('id', 0).execute()
    
    data = {
        'upi_id': context.user_data['upi_id'],
        'name': upi_name,
        'is_active': True,
        'created_at': datetime.utcnow().isoformat()
    }
    
    result = supabase.table('upi_config').insert(data).execute()
    
    if result.data:
        await update.message.reply_text(
            f"✅ *UPI Added Successfully!*\n\n"
            f"🆔 UPI ID: `{data['upi_id']}`\n"
            f"👤 Name: {data['name']}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("❌ Error adding UPI!")
    
    return ConversationHandler.END

# === ADMIN MANAGEMENT (SUPER ADMIN ONLY) ===
async def admin_admins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admins management menu"""
    query = update.callback_query
    await query.answer()
    
    if not is_super_admin(update.effective_user.id):
        await query.message.reply_text("❌ Unauthorized! Super admin access only.")
        return
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Admin", callback_data="admin_add_admin")],
        [InlineKeyboardButton("📋 View Admins", callback_data="admin_view_admins")],
        [InlineKeyboardButton("« Back", callback_data="admin_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(
        "👥 *Admin Management*\n\nSelect an option:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def admin_view_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View all admins"""
    query = update.callback_query
    await query.answer()
    
    admins = supabase.table('admins').select('*').execute().data
    
    if not admins:
        await query.message.reply_text("No admins found!")
        return
    
    text = "👥 *All Admins:*\n\n"
    for admin in admins:
        status = "✅" if admin['is_active'] else "❌"
        role = admin.get('role', 'limited').upper()
        text += f"{status} ID: `{admin['telegram_id']}` | Role: {role}\n"
    
    keyboard = [[InlineKeyboardButton("« Back", callback_data="admin_admins")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(text, parse_mode='Markdown', reply_markup=reply_markup)

async def admin_add_admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding admin"""
    query = update.callback_query
    await query.answer()
    
    if not is_super_admin(update.effective_user.id):
        await query.message.reply_text("❌ Unauthorized!")
        return ConversationHandler.END
    
    await query.message.reply_text(
        "➕ *Add New Admin*\n\nEnter Telegram User ID:",
        parse_mode='Markdown'
    )
    
    return ADMIN_ADD_ADMIN_ID

async def admin_add_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive admin ID and ask for role"""
    try:
        admin_id = int(update.message.text.strip())
        context.user_data['new_admin_id'] = admin_id
        
        keyboard = [
            [InlineKeyboardButton("🔑 Super Admin", callback_data="role_super")],
            [InlineKeyboardButton("🔐 Limited Admin", callback_data="role_limited")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"Select admin role:\n\n"
            f"🔑 *Super Admin* - Full access\n"
            f"🔐 *Limited Admin* - Only generate tokens & review",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        
        return ADMIN_ADD_ADMIN_ROLE
    except:
        await update.message.reply_text("❌ Invalid User ID! Please enter a number:")
        return ADMIN_ADD_ADMIN_ID

async def admin_add_admin_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive role and save admin"""
    query = update.callback_query
    await query.answer()
    
    role = query.data.split('_')[1]  # 'super' or 'limited'
    admin_id = context.user_data.get('new_admin_id')
    
    data = {
        'telegram_id': admin_id,
        'role': role,
        'is_active': True,
        'created_at': datetime.utcnow().isoformat()
    }
    
    result = supabase.table('admins').insert(data).execute()
    
    if result.data:
        role_name = "Super Admin" if role == 'super' else "Limited Admin"
        await query.message.reply_text(
            f"✅ *Admin Added Successfully!*\n\n"
            f"🆔 User ID: `{admin_id}`\n"
            f"👤 Role: {role_name}",
            parse_mode='Markdown'
        )
    else:
        await query.message.reply_text("❌ Error adding admin!")
    
    return ConversationHandler.END

# === BOT SETTINGS (SUPER ADMIN ONLY) ===
async def admin_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot settings menu"""
    query = update.callback_query
    await query.answer()
    
    if not is_super_admin(update.effective_user.id):
        await query.message.reply_text("❌ Unauthorized! Super admin access only.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📢 Edit Force Channel", callback_data="admin_edit_channel")],
        [InlineKeyboardButton("🔑 Edit API Token", callback_data="admin_edit_api")],
        [InlineKeyboardButton("📋 View Settings", callback_data="admin_view_settings")],
        [InlineKeyboardButton("« Back", callback_data="admin_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(
        "⚙️ *Bot Settings*\n\nSelect an option:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def admin_view_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View current settings"""
    query = update.callback_query
    await query.answer()
    
    settings = supabase.table('bot_settings').select('*').limit(1).execute().data
    
    if not settings:
        await query.message.reply_text("No settings found!")
        return
    
    setting = settings[0]
    text = (
        "⚙️ *Current Settings:*\n\n"
        f"📢 Force Channel: {setting.get('force_channel', 'Not set')}\n"
        f"🔑 API Token: `{setting.get('api_token', 'Not set')}`"
    )
    
    keyboard = [[InlineKeyboardButton("« Back", callback_data="admin_settings")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(text, parse_mode='Markdown', reply_markup=reply_markup)

async def admin_edit_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start editing channel"""
    query = update.callback_query
    await query.answer()
    
    if not is_super_admin(update.effective_user.id):
        await query.message.reply_text("❌ Unauthorized!")
        return ConversationHandler.END
    
    await query.message.reply_text(
        "📢 *Edit Force Channel*\n\n"
        "Enter channel username (with @) or channel ID:",
        parse_mode='Markdown'
    )
    
    return ADMIN_EDIT_CHANNEL

async def admin_edit_channel_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save channel"""
    channel = update.message.text.strip()
    
    # Handle channel ID vs username
    if channel.startswith('-100'):
        # It's a channel ID, don't add @
        channel = channel
    elif not channel.startswith('@'):
        # It's a username without @, add it
        channel = '@' + channel
    
    settings = supabase.table('bot_settings').select('*').limit(1).execute().data
    
    if settings:
        supabase.table('bot_settings').update({'force_channel': channel}).eq('id', settings[0]['id']).execute()
    else:
        supabase.table('bot_settings').insert({'force_channel': channel}).execute()
    
    channel_type = "Channel ID" if channel.startswith('-') else "Channel Username"
    
    await update.message.reply_text(
        f"✅ *Channel Updated!*\n\n"
        f"📢 Type: {channel_type}\n"
        f"📢 Value: `{channel}`\n\n"
        f"⚠️ Make sure bot is added as ADMIN in the channel!",
        parse_mode='Markdown'
    )
    
    return ConversationHandler.END

async def admin_edit_api_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start editing API token"""
    query = update.callback_query
    await query.answer()
    
    if not is_super_admin(update.effective_user.id):
        await query.message.reply_text("❌ Unauthorized!")
        return ConversationHandler.END
    
    await query.message.reply_text(
        "🔑 *Edit API Token*\n\n"
        "Enter new API token:",
        parse_mode='Markdown'
    )
    
    return ADMIN_EDIT_API

async def admin_edit_api_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save API token"""
    api_token = update.message.text.strip()
    
    settings = supabase.table('bot_settings').select('*').limit(1).execute().data
    
    if settings:
        supabase.table('bot_settings').update({'api_token': api_token}).eq('id', settings[0]['id']).execute()
    else:
        supabase.table('bot_settings').insert({'api_token': api_token}).execute()
    
    await update.message.reply_text(
        f"✅ *API Token Updated!*\n\n🔑 Token: `{api_token}`",
        parse_mode='Markdown'
    )
    
    return ConversationHandler.END

# === STATISTICS ===
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show statistics"""
    query = update.callback_query
    await query.answer()
    
    total_users = supabase.table('users').select('*', count='exact').execute().count
    total_tokens = supabase.table('tokens').select('*', count='exact').execute().count
    active_tokens = supabase.table('tokens').select('*', count='exact').eq('status', 'active').execute().count
    total_keys = supabase.table('keys').select('*', count='exact').execute().count
    pending_reviews = supabase.table('pending_transactions').select('*', count='exact').eq('status', 'pending').execute().count
    
    text = (
        f"📊 *Statistics*\n\n"
        f"👥 Total Users: {total_users}\n"
        f"🎟 Total Tokens: {total_tokens}\n"
        f"✅ Active Tokens: {active_tokens}\n"
        f"🔐 Total Keys: {total_keys}\n"
        f"⏳ Pending Reviews: {pending_reviews}"
    )
    
    keyboard = [[InlineKeyboardButton("« Back", callback_data="admin_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(text, parse_mode='Markdown', reply_markup=reply_markup)

# === HELPER FUNCTIONS ===
async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back to main admin panel"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    role = get_admin_role(user_id)
    
    if role == 'limited':
        # Limited admin menu
        keyboard = [
            [InlineKeyboardButton("➕ Generate Token Manually", callback_data="admin_gen_token")],
            [InlineKeyboardButton("📋 View Pending Reviews", callback_data="admin_pending")],
            [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.edit_text(
            "🔐 *Limited Admin Panel*\n\nSelect an option:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    else:
        # Super admin menu
        keyboard = [
            [InlineKeyboardButton("➕ Generate Token Manually", callback_data="admin_gen_token")],
            [InlineKeyboardButton("📋 View Pending Reviews", callback_data="admin_pending")],
            [InlineKeyboardButton("📦 Manage Packages", callback_data="admin_packages")],
            [InlineKeyboardButton("💳 Manage UPI", callback_data="admin_upi")],
            [InlineKeyboardButton("👥 Manage Admins", callback_data="admin_admins")],
            [InlineKeyboardButton("⚙️ Bot Settings", callback_data="admin_settings")],
            [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.edit_text(
            "🔑 *Super Admin Panel*\n\nSelect an option:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel operation"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.reply_text("❌ Cancelled!")
    else:
        await update.message.reply_text("❌ Cancelled!")
    
    return ConversationHandler.END

# Export handlers
def get_admin_handlers():
    """Return all admin handlers"""
    
    # Manual token generation
    token_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_generate_token_callback, pattern="^admin_gen_token$")],
        states={
            ADMIN_WAITING_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_receive_username)],
            ADMIN_WAITING_PACKAGE: [CallbackQueryHandler(admin_select_package, pattern="^admin_pkg_")],
            ADMIN_WAITING_TXN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_receive_transaction)]
        },
        fallbacks=[CallbackQueryHandler(admin_cancel, pattern="^admin_cancel$")]
    )
    
    # Add package
    pkg_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_package_start, pattern="^admin_add_package$")],
        states={
            ADMIN_ADD_PKG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_package_name)],
            ADMIN_ADD_PKG_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_package_desc)],
            ADMIN_ADD_PKG_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_package_amount)],
            ADMIN_ADD_PKG_VALIDITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_package_validity)]
        },
        fallbacks=[CallbackQueryHandler(admin_cancel, pattern="^admin_cancel$")]
    )
    
    # Add UPI
    upi_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_upi_start, pattern="^admin_add_upi$")],
        states={
            ADMIN_ADD_UPI_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_upi_id)],
            ADMIN_ADD_UPI_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_upi_name)]
        },
        fallbacks=[CallbackQueryHandler(admin_cancel, pattern="^admin_cancel$")]
    )
    
    # Add Admin
    admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_admin_start, pattern="^admin_add_admin$")],
        states={
            ADMIN_ADD_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_admin_id)],
            ADMIN_ADD_ADMIN_ROLE: [CallbackQueryHandler(admin_add_admin_role, pattern="^role_")]
        },
        fallbacks=[CallbackQueryHandler(admin_cancel, pattern="^admin_cancel$")]
    )
    
    # Edit Channel
    channel_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_edit_channel_start, pattern="^admin_edit_channel$")],
        states={
            ADMIN_EDIT_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_channel_save)]
        },
        fallbacks=[CallbackQueryHandler(admin_cancel, pattern="^admin_cancel$")]
    )
    
    # Edit API
    api_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_edit_api_start, pattern="^admin_edit_api$")],
        states={
            ADMIN_EDIT_API: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_api_save)]
        },
        fallbacks=[CallbackQueryHandler(admin_cancel, pattern="^admin_cancel$")]
    )
    
    return [
        CommandHandler("admin", admin_panel),
        token_conv,
        pkg_conv,
        upi_conv,
        admin_conv,
        channel_conv,
        api_conv,
        CallbackQueryHandler(admin_view_pending, pattern="^admin_pending$"),
        CallbackQueryHandler(admin_packages_menu, pattern="^admin_packages$"),
        CallbackQueryHandler(admin_view_packages, pattern="^admin_view_packages$"),
        CallbackQueryHandler(admin_upi_menu, pattern="^admin_upi$"),
        CallbackQueryHandler(admin_view_upi, pattern="^admin_view_upi$"),
        CallbackQueryHandler(admin_admins_menu, pattern="^admin_admins$"),
        CallbackQueryHandler(admin_view_admins, pattern="^admin_view_admins$"),
        CallbackQueryHandler(admin_settings_menu, pattern="^admin_settings$"),
        CallbackQueryHandler(admin_view_settings, pattern="^admin_view_settings$"),
        CallbackQueryHandler(admin_stats, pattern="^admin_stats$"),
        CallbackQueryHandler(admin_approve_screenshot, pattern="^approve_"),
        CallbackQueryHandler(admin_reject_screenshot, pattern="^reject_"),
        CallbackQueryHandler(admin_back, pattern="^admin_back$")
    ]

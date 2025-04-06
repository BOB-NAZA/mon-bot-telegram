import logging
from telegram import Update, InputMediaPhoto, InputMediaVideo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater, 
    CommandHandler, 
    CallbackContext, 
    CallbackQueryHandler,
    MessageHandler,
    Filters
)
import schedule
import time
from threading import Thread
from datetime import datetime, time as dt_time
import pytz
import json
import os

# Configuration initiale
CONFIG_FILE = 'bot_config.json'
TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(id) for id in os.getenv('ADMIN_IDS', '').split(',') if id]  
# Remplacez par votre token

# Configuration du logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class PublicationBot:
    def __init__(self, token):
        self.token = token
        self.updater = Updater(token=self.token, use_context=True)
        self.dispatcher = self.updater.dispatcher
        self.load_config()
        
        # États pour la gestion des publications
        self.waiting_for_message = {}
        self.waiting_for_time = {}
        self.waiting_for_media = {}
        
        # Ajout des handlers
        self.dispatcher.add_handler(CommandHandler("start", self.start))
        self.dispatcher.add_handler(CommandHandler("ajouter", self.ajouter_groupe))
        self.dispatcher.add_handler(CommandHandler("supprimer", self.supprimer_groupe))
        self.dispatcher.add_handler(CommandHandler("programmer", self.programmer_publication))
        self.dispatcher.add_handler(CommandHandler("liste", self.liste_groupes))
        self.dispatcher.add_handler(CommandHandler("admin", self.admin_panel))
        self.dispatcher.add_handler(CommandHandler("publier", self.publier_immediat))
        
        # Handlers pour l'interface admin
        self.dispatcher.add_handler(CallbackQueryHandler(self.handle_admin_callback, pattern='^admin_'))
        self.dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_message))
        self.dispatcher.add_handler(MessageHandler(Filters.photo | Filters.video, self.handle_media))
        
        # Démarrer le thread de planification
        self.schedule_thread = Thread(target=self.run_scheduler)
        self.schedule_thread.daemon = True
        self.schedule_thread.start()
    
    def is_admin(self, user_id):
        """Vérifie si l'utilisateur est admin"""
        return user_id in ADMIN_IDS
    
    def load_config(self):
    """Charge la configuration depuis le fichier JSON"""
    default_config = {
        "groupes": [],
        "publications": [],
        "programmation": {}
    }
    
    try:
        # Vérifie si le fichier existe et n'est pas vide
        if os.path.exists(CONFIG_FILE) and os.path.getsize(CONFIG_FILE) > 0:
            with open(CONFIG_FILE, 'r') as f:
                self.config = json.load(f)
        else:
            self.config = default_config
            self.save_config()
    except json.JSONDecodeError:
        # Si le fichier est corrompu, on le réinitialise
        self.config = default_config
        self.save_config()
    
    def save_config(self):
        """Sauvegarde la configuration dans le fichier JSON"""
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=4)
    
    def start(self, update: Update, context: CallbackContext):
        """Handler pour la commande /start"""
        user = update.effective_user
        update.message.reply_text(
            f"Bonjour {user.first_name}!\n\n"
            "Je suis un bot de diffusion de publications personnalisées.\n\n"
            "Commandes disponibles:\n"
            "/ajouter - Ajouter un groupe/canal\n"
            "/supprimer - Supprimer un groupe/canal\n"
            "/programmer - Programmer une publication\n"
            "/liste - Lister les groupes/canaux enregistrés\n"
            "/publier - Envoyer une publication immédiate (admin)\n"
            "/admin - Panel d'administration (admin)"
        )
    
    def admin_panel(self, update: Update, context: CallbackContext):
        """Affiche le panel d'administration"""
        if not self.is_admin(update.effective_user.id):
            update.message.reply_text("❌ Accès réservé aux administrateurs.")
            return
        
        keyboard = [
            [InlineKeyboardButton("📝 Liste des publications", callback_data='admin_list_pubs')],
            [InlineKeyboardButton("➕ Ajouter une publication", callback_data='admin_add_pub')],
            [InlineKeyboardButton("✏️ Modifier une publication", callback_data='admin_edit_pub')],
            [InlineKeyboardButton("❌ Supprimer une publication", callback_data='admin_delete_pub')],
            [InlineKeyboardButton("📊 Statistiques", callback_data='admin_stats')]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(
            "🛠 Panel d'administration:",
            reply_markup=reply_markup
        )
    
    def handle_admin_callback(self, update: Update, context: CallbackContext):
        """Gère les actions du panel admin"""
        query = update.callback_query
        query.answer()
        user_id = query.from_user.id
        
        if not self.is_admin(user_id):
            query.edit_message_text("❌ Accès réservé aux administrateurs.")
            return
        
        action = query.data.split('_')[1]
        
        if action == 'list':
            self.show_publications_list(query)
        elif action == 'add':
            self.start_add_publication(query)
        elif action == 'edit':
            self.start_edit_publication(query)
        elif action == 'delete':
            self.start_delete_publication(query)
        elif action == 'stats':
            self.show_stats(query)
        elif action == 'confirmadd':
            self.confirm_add_publication(query)
        elif action == 'cancel':
            query.edit_message_text("❌ Action annulée.")
    
    def show_publications_list(self, query):
        """Affiche la liste des publications programmées"""
        if not self.config['publications']:
            query.edit_message_text("Aucune publication programmée.")
            return
        
        message = "📋 Publications programmées:\n\n"
        for idx, pub in enumerate(self.config['publications'], 1):
            message += (
                f"{idx}. ⏰ {pub['heure']}\n"
                f"   📝 {pub['message'][:50]}...\n"
                f"   {'✅ Actif' if self.config['programmation'].get(pub['id'], {}).get('active', False) else '❌ Inactif'}\n\n"
            )
        
        query.edit_message_text(message)
    
    def start_add_publication(self, query):
        """Commence le processus d'ajout d'une publication"""
        self.waiting_for_message[query.from_user.id] = True
        query.edit_message_text(
            "✍️ Envoyez-moi le message que vous voulez programmer (texte seul).\n\n"
            "Pour annuler, envoyez /cancel"
        )
    
    def handle_message(self, update: Update, context: CallbackContext):
        """Gère les messages texte pour la création de publications"""
        user_id = update.effective_user.id
        
        if user_id in self.waiting_for_message:
            # Étape 1: Récupérer le message
            self.waiting_for_message[user_id] = {
                'message': update.message.text,
                'media': []
            }
            self.waiting_for_time[user_id] = True
            update.message.reply_text(
                "🕒 À quelle heure voulez-vous programmer cette publication? (format HH:MM)\n\n"
                "Exemple: 08:30\n\n"
                "Pour annuler, envoyez /cancel"
            )
        elif user_id in self.waiting_for_time:
            # Étape 2: Récupérer l'heure
            try:
                heure = dt_time.fromisoformat(update.message.text)
                pub_data = self.waiting_for_message[user_id]
                pub_data['heure'] = update.message.text
                
                # Demander des médias optionnels
                keyboard = [
                    [InlineKeyboardButton("➕ Ajouter une photo/vidéo", callback_data='admin_addmedia')],
                    [InlineKeyboardButton("✅ Terminer", callback_data='admin_confirmadd')],
                    [InlineKeyboardButton("❌ Annuler", callback_data='admin_cancel')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                update.message.reply_text(
                    "📝 Récapitulatif de la publication:\n\n"
                    f"⏰ Heure: {pub_data['heure']}\n"
                    f"📝 Message: {pub_data['message']}\n"
                    f"🖼 Médias: {len(pub_data['media'])} fichier(s)\n\n"
                    "Voulez-vous ajouter des médias?",
                    reply_markup=reply_markup
                )
                
                # Nettoyer les états temporaires
                del self.waiting_for_message[user_id]
                del self.waiting_for_time[user_id]
                self.waiting_for_media[user_id] = pub_data
                
            except ValueError:
                update.message.reply_text("Format d'heure invalide. Utilisez HH:MM. Réessayez:")
    
    def handle_media(self, update: Update, context: CallbackContext):
        """Gère l'ajout de médias à une publication"""
        user_id = update.effective_user.id
        
        if user_id not in self.waiting_for_media:
            return
        
        pub_data = self.waiting_for_media[user_id]
        
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            pub_data['media'].append({
                'type': 'photo',
                'file_id': file_id
            })
            update.message.reply_text("✅ Photo ajoutée! Vous pouvez en ajouter d'autres ou terminer.")
        elif update.message.video:
            file_id = update.message.video.file_id
            pub_data['media'].append({
                'type': 'video',
                'file_id': file_id
            })
            update.message.reply_text("✅ Vidéo ajoutée! Vous pouvez en ajouter d'autres ou terminer.")
    
    def confirm_add_publication(self, query):
        """Confirme et ajoute la nouvelle publication"""
        user_id = query.from_user.id
        
        if user_id not in self.waiting_for_media:
            query.edit_message_text("❌ Erreur: données de publication introuvables.")
            return
        
        pub_data = self.waiting_for_media[user_id]
        
        # Créer la publication
        publication_id = f"pub_{datetime.now().timestamp()}"
        new_publication = {
            'id': publication_id,
            'heure': pub_data['heure'],
            'message': pub_data['message'],
            'media': pub_data['media']
        }
        
        # Ajouter à la configuration
        self.config['publications'].append(new_publication)
        self.config['programmation'][publication_id] = {
            'active': True,
            'derniere_execution': None
        }
        self.save_config()
        
        # Nettoyer
        del self.waiting_for_media[user_id]
        
        query.edit_message_text(
            "✅ Publication ajoutée avec succès!\n\n"
            f"⏰ Heure: {pub_data['heure']}\n"
            f"📝 Message: {pub_data['message']}\n"
            f"🖼 Médias: {len(pub_data['media'])} fichier(s)"
        )
        
        # Replanifier les publications
        self.planifier_publications()
    
    def publier_immediat(self, update: Update, context: CallbackContext):
        """Envoie une publication immédiate à tous les groupes"""
        if not self.is_admin(update.effective_user.id):
            update.message.reply_text("❌ Accès réservé aux administrateurs.")
            return
        
        if not context.args:
            update.message.reply_text(
                "Utilisation: /publier \"message\"\n\n"
                "Exemple: /publier \"Ceci est une publication immédiate!\""
            )
            return
        
        message = ' '.join(context.args)
        publication = {
            'message': message,
            'media': []  # Vous pouvez étendre cette fonction pour gérer les médias
        }
        
        # Envoyer immédiatement
        self.envoyer_publication(publication)
        
        update.message.reply_text(f"✅ Publication envoyée immédiatement à tous les groupes!\n\nMessage: {message}")
    
    # ... (les autres méthodes restent les mêmes que dans la version précédente)

if __name__ == '__main__':
    bot = PublicationBot(TOKEN)
    bot.run()

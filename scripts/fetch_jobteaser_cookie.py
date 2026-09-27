"""
Script Playwright pour auto-renouveler les cookies JobTeaser.
Il utilise un contexte persistant. S'il n'est pas connecté, il met en pause
l'exécution pour vous laisser vous connecter. Ensuite, il extrait les cookies
et met à jour le fichier .env automatiquement.
"""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

def update_env_file(cookies_str: str) -> None:
    env_path = Path(".env")
    if not env_path.exists():
        print("Fichier .env introuvable.")
        return

    content = env_path.read_text(encoding="utf-8")
    
    # Remplacer JOBTEASER_COOKIES
    content = re.sub(r'JOBTEASER_COOKIES=.*', f'JOBTEASER_COOKIES={cookies_str}', content)
    
    # Extraire session et cf_clearance si présents
    m_sess = re.search(r'jobteaser_session=([^;]+)', cookies_str)
    if m_sess:
        content = re.sub(r'JOBTEASER_SESSION=.*', f'JOBTEASER_SESSION={m_sess.group(1)}', content)
        
    m_cf = re.search(r'cf_clearance=([^;]+)', cookies_str)
    if m_cf:
        content = re.sub(r'JOBTEASER_CF_CLEARANCE=.*', f'JOBTEASER_CF_CLEARANCE={m_cf.group(1)}', content)
        
    env_path.write_text(content, encoding="utf-8")
    print("\n✅ Fichier .env mis à jour avec succès avec les nouveaux cookies !")

def fetch_cookies():
    user_data_dir = os.path.join(os.getcwd(), ".playwright_profile")
    
    print("🚀 Lancement de Playwright...")
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False, # Toujours visible pour gérer Cloudflare ou la connexion manuelle
        )
        page = browser.new_page()
        
        print("🔗 Navigation vers JobTeaser...")
        page.goto("https://mines-ales.jobteaser.com/fr/dashboard")
        
        # On attend que l'utilisateur soit connecté et que le dashboard soit chargé
        try:
            # On vérifie si la page de login est présente
            if "users/sign_in" in page.url or "cas.mines-ales.fr" in page.url:
                print("⚠️ Connexion requise. Veuillez vous connecter dans la fenêtre du navigateur.")
                print("⏳ En attente de la redirection vers le dashboard...")
                page.wait_for_url("**/dashboard", timeout=300000) # 5 minutes max
        except Exception:
            pass

        print("🍪 Extraction des cookies...")
        playwright_cookies = browser.cookies()
        
        cookie_parts = []
        for c in playwright_cookies:
            if "jobteaser.com" in c["domain"]:
                cookie_parts.append(f"{c['name']}={c['value']}")
        
        cookie_string = "; ".join(cookie_parts)
        
        if "cf_clearance" not in cookie_string or "jobteaser_session" not in cookie_string:
            print("❌ Attention: Il manque 'cf_clearance' ou 'jobteaser_session' dans les cookies. La protection Cloudflare est-elle toujours active ?")
            # Optionnel: on peut mettre la page en pause pour passer manuellement le captcha Cloudflare
            # page.pause()
            
        update_env_file(cookie_string)
        browser.close()

if __name__ == "__main__":
    fetch_cookies()

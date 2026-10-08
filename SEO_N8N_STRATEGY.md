# SEO naturel TranscribeAI et automatisation n8n

## Verdict

L’idée est bonne si le workflow reproduit un vrai travail marketing : recherche de besoins, amélioration des pages, rédaction originale, distribution et analyse des résultats. Il ne faut pas faire tourner n8n en boucle pour fabriquer des centaines de pages ou des backlinks artificiels. Google considère la production automatisée de nombreuses pages sans valeur originale comme du *scaled content abuse*.

La bonne approche est donc un service n8n disponible 24 h/24, avec des exécutions planifiées et événementielles, pas un robot qui publie sans contrôle toute la journée.

## SEO technique déjà préparé

Le site contient maintenant :

- un titre SEO descriptif ;
- une meta description ;
- une URL canonique sur `https://transcribeai.site/` ;
- des balises Open Graph et Twitter Card ;
- des données structurées `SoftwareApplication` ;
- un contenu public expliquant le produit et son fonctionnement ;
- une FAQ visible dans le HTML initial ;
- `/robots.txt` avec blocage des routes privées `/api/` et `/auth/` ;
- `/sitemap.xml` avec l’URL publique canonique ;
- une structure H1/H2 lisible sans exécuter JavaScript.

### Pages SEO publiées dans le code

Le site expose maintenant des pages éditoriales dédiées, chacune avec son propre title, meta description, canonical, données structurées, contenu original et liens internes :

- `/youtube-transcription`
- `/tiktok-transcription`
- `/instagram-transcription`
- `/video-to-text`
- `/audio-to-text`
- `/ai-subtitle-generator`
- `/arabic-transcription`
- `/french-transcription`

Le sitemap FastAPI les inclut automatiquement. Ne pas multiplier ces pages sans nouvelle intention de recherche ou contenu réellement distinct.

## Workflow n8n recommandé

### Workflow A, surveillance quotidienne

Déclencheur : tous les jours à 08:00.

1. **Schedule Trigger**
2. **Google Search Console API** : récupérer clics, impressions, CTR et positions des requêtes des 28 derniers jours.
3. **Google Analytics 4** : récupérer sessions, conversions d’inscription et événements de transcription.
4. **Code node** : détecter les requêtes avec impressions mais CTR faible, les pages en baisse et les pages sans conversion.
5. **LLM** : proposer des améliorations de titre, meta description, FAQ ou liens internes uniquement à partir des données reçues.
6. **Google Sheets ou Notion** : enregistrer les recommandations avec statut `proposed`.
7. **Email ou Slack** : envoyer un résumé hebdomadaire à l’administrateur.

Ce workflow ne publie rien automatiquement. Il aide à décider quoi améliorer.

### Workflow B, production éditoriale hebdomadaire

Déclencheur : lundi à 09:00.

1. **Schedule Trigger**
2. **Google Search Console** : choisir une requête réellement recherchée et liée à la transcription vidéo.
3. **HTTP Request** : vérifier les résultats concurrents et les sources publiques autorisées.
4. **LLM** : produire un brief, pas un article final, avec intention de recherche, questions à traiter, sources et angle original.
5. **Notion ou Google Docs** : créer une fiche éditoriale.
6. **Validation humaine obligatoire**.
7. Après validation : créer une branche GitHub ou une Pull Request contenant la page.
8. **GitHub** : lancer les tests SEO et HTML.
9. **Render Deploy Hook** : déployer uniquement après validation et fusion.
10. **Google Search Console API** : soumettre le sitemap après le déploiement, sans demander une indexation agressive de chaque URL.

### Workflow C, distribution après publication

Déclencheur : nouvelle page publiée dans GitHub ou nouvelle ligne `published` dans Notion.

1. Récupérer le titre, l’URL et le résumé de la page.
2. Générer une version courte adaptée à LinkedIn, X, Reddit ou newsletter.
3. Ajouter l’URL canonique et une formulation non trompeuse.
4. Envoyer les propositions en validation humaine.
5. Publier uniquement sur les comptes connectés et autorisés.
6. Enregistrer les URLs publiées dans une feuille de suivi.

Le workflow ne doit pas publier le même texte sur tous les réseaux ni déposer automatiquement des commentaires dans des forums.

### Workflow D, contrôle technique chaque semaine

1. Appeler `https://transcribeai.site/`, `/robots.txt` et `/sitemap.xml`.
2. Vérifier le statut HTTP, le canonical, le title, la meta description, le H1, les données structurées et les liens cassés.
3. Comparer le sitemap avec les pages publiques attendues.
4. Envoyer une alerte si le site retourne une erreur, si le sitemap est invalide ou si une page publique devient `noindex`.

## Structure éditoriale conseillée

Commencer par 6 à 10 pages utiles, écrites pour des besoins réels :

- transcription YouTube en ligne ;
- transcription TikTok ;
- créer des sous-titres SRT et VTT ;
- résumer une vidéo avec l’IA ;
- transcription d’interview ;
- transcription multilingue ;
- guide de précision et limites de la transcription automatique ;
- comparaison claire des offres TranscribeAI.

Chaque page doit avoir une intention distincte, des exemples originaux, des captures ou démonstrations réelles quand elles apportent de la valeur, des liens vers la page d’accueil et aucune promesse que le produit ne tient pas.

## Garde-fous obligatoires

- Ne pas générer automatiquement des centaines de pages presque identiques.
- Ne pas scraper et republier les articles d’autres sites.
- Ne pas acheter ou fabriquer des backlinks.
- Ne pas publier sans relecture humaine les contenus qui parlent de prix, confidentialité, paiements ou fonctionnalités.
- Ne pas exposer les pages privées, les transcriptions utilisateurs, les emails ou les routes API dans le sitemap.
- Conserver un journal des prompts, sources, validations et versions publiées.
- Limiter la fréquence des appels Google et des publications sociales.

## Déploiement n8n

Pour un vrai fonctionnement permanent, héberger n8n sur un service persistant avec HTTPS, une base de données, sauvegardes et variables secrètes. Le workflow doit utiliser des identifiants n8n pour Google Search Console, GA4, GitHub, Render, email et les réseaux sociaux. Les tokens ne doivent jamais être écrits dans les nodes ou dans GitHub.

Une instance n8n 24 h/24 signifie que le service est toujours disponible ; les tâches doivent rester planifiées à quelques moments utiles. Une boucle permanente serait plus coûteuse, plus fragile et moins conforme aux bonnes pratiques.

## Sources officielles

- [Google SEO Starter Guide](https://developers.google.com/search/docs/fundamentals/seo-starter-guide)
- [Google Search spam policies](https://developers.google.com/search/docs/essentials/spam-policies)
- [Google sitemap documentation](https://developers.google.com/search/docs/crawling-indexing/sitemaps/overview)

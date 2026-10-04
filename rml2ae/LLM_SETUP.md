# Configurer son LLM pour rml2ae

Deux couches : **le Rive CLI se documente lui-même** pour l'agent, et **rml2ae ajoute une skill** pour la partie After Effects.

## 1. Le projet Rive CLI (côté Rive)
`rive create <dossier>` écrit `AGENTS.md` + `CLAUDE.md` dans le projet : ils disent à l'agent d'utiliser `rive docs`,
`rive schema`, `rive . --verify`, `rive inspect`, `--screenshot`, et de ne jamais deviner un type ou une propriété.
Ne pas les supprimer. Pour des scènes ambitieuses (films, jeux), ajouter au projet ou à la mémoire de l'agent
`docs/RIVE_CLI_MCP_KNOWLEDGE.md` (pièges mesurés du CLI : clés, os, layouts, Luau, WGSL, push) et
`docs/RIVE_FILM_GUIDELINES.md` (méthode board → RML → rendu).

## 2. Claude Code (recommandé)
- **Skill** : copier `skill/rml2ae` dans `~/.claude/skills/` (l'installeur le propose) → Claude charge la skill dès
  qu'il est question d'After Effects et connaît les commandes `ae`, les règles (jamais AE sans ton accord, lire le
  rapport, RML = source de vérité) et ce que la conversion produit.
- **Dans le projet** : ajouter à `CLAUDE.md` (créé par `rive create`) :
  ```
  ## After Effects
  Ce projet se convertit en After Effects avec `ae` (rml2ae, skill `rml2ae`). Ne lance jamais `ae build`, `ae watch`
  ou `ae pull` sans me le demander : ils agissent sur l'After Effects que j'ai ouvert. Après un build, lis
  build/rml2ae/<nom>.ae-report.md et dis-moi ce qui est approximé.
  ```
- Le bouton du panneau AE (Fenêtre › Rive.jsx) reste le moyen de mettre à jour AE **quand tu le décides** ; l'agent
  écrit le RML, tu regardes dans le viewer Rive, tu cliques.

## 3. Autres agents (Codex, Cursor, Gemini…)
Même contenu que la skill, dans leur fichier d'instructions : coller `skill/rml2ae/SKILL.md` (sans l'en-tête `---`)
dans `AGENTS.md` du projet ou dans les règles globales de l'outil. Aucun MCP n'est nécessaire : tout passe par la
ligne de commande (`ae …`) et des fichiers (RML, rapport, log).

## 4. Ce qu'il ne faut pas donner à l'agent
- Pas d'accès direct à After Effects (MCP AE, scripts ad hoc) : tout ce qu'AE doit faire passe par `ae`, qui est
  déterministe et journalisé (`build/rml2ae/<nom>.ae.log`). Un agent qui bricole AE en direct casse les tags
  `rive:<id>` dont dépendent l'incrémental et le pull.
- Pas de `.riv`/`.aep` à écrire à la main : ils sont produits par le Rive CLI et par AE.

# Ollama Translator

[![CI](https://github.com/minyiyang/ollama-translator/actions/workflows/ci.yml/badge.svg)](https://github.com/minyiyang/ollama-translator/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | **Français** | [Español](README.es.md) | [Deutsch](README.de.md) | [한국어](README.ko.md)

Un pipeline local de traduction de livres entiers, avec reprise sur
interruption, conçu pour la prose littéraire de l'anglais vers le chinois et
du chinois vers l'anglais. Il prend en entrée un fichier EPUB, RTF, texte,
Markdown, HTML, Word (.docx) ou un PDF contenant du texte, et produit un EPUB
traduit, qui peut aussi être exporté en Word, HTML, Markdown ou texte. Il
traduit également les fichiers de sous-titres (.srt, .vtt, .ass), réplique
par réplique, sans toucher au minutage. Tout s'exécute avec des modèles
Ollama locaux : pas d'API cloud, pas de serveur MCP, pas de framework
d'agents du type LangGraph ou AutoGen.

Les paires anglais → chinois simplifié (`en-zh`) et chinois → anglais
(`zh-en`) sont optimisées : tous les contrôles, le glossaire, la feuille de
style et la réécriture stylistique leur sont appliqués. Le français (`fr`),
le japonais (`ja`), l'espagnol (`es`), l'allemand (`de`) et le coréen (`ko`)
disposent d'un profil (conventions de ponctuation, formes d'adresse, nombres
en toutes lettres, exemples de prompts), comme langue source ou cible, quelle
que soit l'autre langue. Toute autre langue est acceptée au niveau générique :
les contrôles qu'elle ne permet pas sont ignorés, et la qualité dépend alors
du modèle (voir [docs/GENERIC_LANGUAGES.md](docs/GENERIC_LANGUAGES.md), en
anglais). La paire se déclare avec `translation.direction: en>ja`, ou avec
`source_language` et `target_language`.

## Quels problèmes ce projet résout

La traduction d'un livre entier n'échoue pas de la même façon qu'un appel
isolé. Quand on confie les chapitres un à un à un grand modèle, on rencontre
en général :

- **une terminologie qui dérive** : le même nom de personne ou de lieu est
  rendu de deux façons au chapitre 3 et au chapitre 20 ;
- **une structure abîmée** : des balises en ligne comme `<em>` ou `<i>`
  disparaissent à la réécriture, et la mise en page de l'EPUB est cassée ;
- **des quantités faussées** : nombres, unités, distances et durées sont
  modifiés sans bruit, d'une manière difficile à repérer à l'œil ;
- **des corrections qui dégradent** : le modèle « corrige » une phrase qui
  était juste et remplace une bonne traduction par une moins bonne ;
- **une interruption qui oblige à tout refaire** : une erreur au chapitre 40,
  et il faut repartir de zéro.

Ce projet traite chacune de ces catégories dans une étape distincte, dotée de
ses propres points de reprise, plutôt que de tout miser sur un prompt plus
long.

## Fonctionnement

```text
decompile
  -> extract_glossary -> resolve_glossary -> approve_glossary
  -> build_story_context (optional)
  -> preprocess -> translate
  -> audit_translation -> audit_consistency -> repair_translation
  -> reprose_translation
  -> review_repaired -> repair_review -> validate_repaired
  -> translate_title -> compile -> validate_epub
```

Chaque étape enregistre ses artefacts dans un espace de travail qui lui est
propre, sous `runs/<job-id>/`. Avec `resume`, les étapes déjà terminées et
validées sont ignorées : la tâche reprend à la première étape concernée, et
non au début.

## Exemple : *Alice au pays des merveilles*

Le livre d'exemple fourni avec le dépôt, issu du Projet Gutenberg, permet de
dérouler tout le processus de l'anglais vers le chinois simplifié. Tout ce
qui suit se passe dans le tableau de bord local, dans le navigateur :

```powershell
book-agent ui          # http://127.0.0.1:8765/
```

> L'interface du tableau de bord évolue encore ; les captures d'écran sont
> omises pour l'instant et seront refaites une fois l'interface stabilisée.

**1. Configurer et lancer.** La page Tâches liste toutes les tâches, dix par
page. *Ajouter une tâche* ne demande que le livre source (à choisir parmi les
fichiers connus, à parcourir ou à déposer), un nom de fichier de
configuration et un identifiant de tâche, qui reprend par défaut le nom de la
configuration ; une configuration existante, par exemple
[`configs/demo-alice.yaml`](configs/demo-alice.yaml), est réutilisée telle
quelle, un nouveau nom part de `config.example.yaml`. La tâche s'ouvre sur
son onglet **Configuration**. *Options* regroupe les paramètres que l'on
modifie le plus souvent (style de traduction, modèle de chaque rôle avec son
état d'installation ✓/✗, mode d'approbation du glossaire, contrôles qualité,
sortie) ; *Tous les paramètres* expose chaque champ de configuration avec son
type, ses bornes, sa valeur par défaut et un bouton de réinitialisation ;
*YAML* modifie directement le fichier, et les trois vues restent
synchronisées. *Valider* enregistre le fichier, le vérifie, exécute la tâche
à blanc et demande à Ollama si tous les modèles sont installés. Le bouton
*Lancer la traduction*, dans l'en-tête de la tâche, n'est disponible que tant
que la configuration n'a pas changé depuis sa validation. Pendant
l'exécution, l'en-tête propose *Pause*, qui laisse l'appel au modèle en cours
se terminer avant de s'arrêter proprement, et *Arrêter*, qui interrompt
l'exécution immédiatement ; dans les deux cas, *Reprendre* repart du dernier
point de reprise. La configuration de démonstration n'utilise que des modèles
installés localement, active la réécriture stylistique et le nettoyage de
l'EPUB, et confie l'approbation du glossaire au modèle. Pour lancer la même
tâche depuis un terminal : `.\scripts\demo-alice.ps1` (`-Resume` pour la
reprendre).

**2. Approbation du glossaire.** Avec `workflow.llm_glossary_review: true`,
les entrées attestées dans le texte, de confiance élevée et sans conflit sont
approuvées directement, les autres sont soumises au relecteur LLM ; la page
Glossaire affiche ensuite les termes approuvés, ceux que le modèle a relus,
pour quelle raison, et ce qu'il a changé. Avec une relecture humaine, la
tâche se met en pause à ce stade et la même page devient un éditeur : on y
corrige la traduction d'un terme, sa catégorie, sa note ou ses alias, on le
rejette, on filtre les entrées signalées (mots génériques, traductions
partagées par plusieurs termes, confiance faible, absence d'attestation) et
on consulte les phrases sources de chaque terme. On approuve ensuite sa
propre version, ou on la transmet (elle ou le brouillon intact) au relecteur
LLM ; dans les deux cas, le pipeline reprend.

**3. Traduction, audit et correction.** La page Progression suit la tâche en
direct, qu'elle ait été lancée depuis le tableau de bord ou depuis un
terminal : chaque étape avec son état, les opérations LLM prévues,
l'unité en cours, les appels, les tokens générés et la durée, ainsi que
l'appel au modèle en cours et le journal de session. Pour ce livre, le
pipeline s'est mis en pause avant la compilation, avec trois segments qu'il
ne pouvait pas vérifier seul.

**4. Relecture finale.** La page Relecture finale (qu'ouvre directement
`book-agent review-ui .\runs\demo-alice-en-zh`) permet de traiter cette file
de relecture. Chaque segment est présenté avec le texte source et son
contexte, les constats de l'audit (un clic surligne le passage cité), les
versions successives produites par le pipeline et un éditeur avec diff en
direct, soumis au même contrôle déterministe que `resolve-review`. Appliquer
les décisions approuve le brouillon ; l'EPUB est ensuite compilé et validé.

**5. Relire et modifier le livre entier.** L'onglet **Texte** présente chaque
chapitre en regard, source et traduction. N'importe quel segment peut être
modifié sur place, avec un motif, le même contrôle déterministe et un diff.
Chaque modification, y compris chaque décision de la Relecture finale, est
un événement d'un même journal des modifications, qu'aucune relance
n'efface : elle peut donc être annulée, et son historique consulté. Si une
relance change la traduction d'un segment modifié, celui-ci passe en conflit
et c'est à vous de trancher. *Recompiler* reconstruit le livre avec les
modifications en quelques secondes. **⤓ Exporter en XLIFF** transmet le
livre à un outil de TAO, et **⤒ Importer un XLIFF** affiche un aperçu du
fichier renvoyé par le traducteur avant toute écriture.

## Choix de conception

- **Contrôle déterministe de ce qui est vérifiable.** La structure de l'EPUB,
  les marqueurs en ligne et les identifiants sont gérés par du code, et non
  laissés à l'appréciation d'un modèle.
- **S'abstenir plutôt que corriger au hasard.** Une correction qui ne peut
  pas être vérifiée est écartée au lieu d'écraser une traduction existante
  meilleure ; le segment est renvoyé en relecture humaine.
- **Des tentatives bornées.** Le pipeline cesse de rappeler le modèle dès que
  la validation ne converge plus, au lieu de tourner en boucle sur le même
  échec.
- **Un traitement générique des échecs.** Les types d'échec sont traités par
  des règles générales ; le code ne contient aucun correctif propre à un
  livre ou à un passage.
- **Un contexte alloué à la demande.** Le contexte de 131K configuré est un
  plafond, pas une allocation. Les appels ordinaires commencent à 16K, la
  correction ciblée et sa vérification à 8K, et ne passent à 16K, 32K, 64K
  puis 131K que si nécessaire, ce qui garde l'occupation de la mémoire GPU
  proportionnelle à chaque requête.

## Démarrage rapide

### 1. Préparer Ollama et les modèles

Ollama doit tourner en local, et les modèles utilisés par la configuration
doivent être installés :

```powershell
ollama pull qwen3.8:latest
ollama pull gemma4:31b
```

Avant la première étape qui fait appel à un modèle, la CLI vérifie le nom des
modèles et la capacité de contexte configurée.

### 2. Installer

```powershell
python -m pip install -e .
book-agent --version
```

L'installation par fichiers requirements est également possible (les
dépendances déclarées dans `pyproject.toml` font foi) :

```powershell
python -m pip install -r requirements.txt
python -m book_agent --version
```

Python 3.11 ou plus récent est requis, ainsi que `ollama>=0.6.2`.

### 3. Copier et vérifier la configuration

```powershell
copy config.example.yaml my-book.yaml
book-agent config --file .\my-book.yaml
```

`config.example.yaml` est un point de départ prudent pour la production, pas
une liste des valeurs par défaut ; plusieurs de ses valeurs diffèrent
volontairement de celles du code. Toutes les clés sont facultatives : une clé
omise prend la valeur par défaut définie dans `book_agent/config.py`.

### 4. Exécuter à blanc, puis lancer

L'exécution à blanc n'écrit aucun fichier et ne contacte pas Ollama :

```powershell
book-agent run "D:\books\source.epub" --config .\my-book.yaml --dry-run
```

Une fois le résultat vérifié, lancez la tâche :

```powershell
book-agent run "D:\books\source.epub" --config .\my-book.yaml --job-id "my-book-en-zh"
```

La commande affiche le chemin de l'espace de travail. Notez-le : toutes les
commandes suivantes s'appliquent à ce répertoire. Les autres formats d'entrée
(RTF, texte, Markdown, HTML, Word, PDF, sous-titres) utilisent la même
commande et le même pipeline.

### 5. Consulter l'état et reprendre

```powershell
book-agent status .\runs\my-book-en-zh
book-agent resume .\runs\my-book-en-zh --plain
```

`--plain` produit une sortie adaptée aux journaux, sans rien changer au
comportement du pipeline.

## Rôles des modèles

La répartition par défaut est prudente, et chaque étape peut recevoir son
propre modèle :

| Tâche | Modèle par défaut |
|---|---|
| Extraction des candidats du glossaire | `qwen3.8:27b` |
| Résolution et relecture de la terminologie | `qwen3.8:latest` |
| Premier jet et correction ciblée | `qwen3.8:latest` |
| Audit sémantique | `gemma4:31b` |
| Contrôle et arbitrage des quantités | `gemma4:26b`, avec escalade vers `gemma4:31b` si nécessaire |
| Comparaison des corrections et vérification bornée | `gemma4:26b` |
| Proposition de réécriture stylistique | `qwen3.8:latest` |
| Vérification de la réécriture stylistique | `gemma4:31b` |

Le mode thinking est désactivé par défaut pour les appels d'audit et de
vérification qui produisent du JSON structuré ; mieux vaut ne l'activer que
si un gain de qualité a été mesuré.

## Relecture et reprise

Avec la valeur par défaut `require_glossary_review: true`, le traitement se
met en pause une fois le brouillon du glossaire produit, dans l'attente d'une
validation humaine :

```powershell
book-agent approve "D:\runs\my-job" --glossary "D:\reviews\glossary.txt" --resume
```

On peut aussi demander une relecture par le modèle, contrainte par un schéma,
et poursuivre aussitôt :

```powershell
book-agent approve "D:\runs\my-job" --llm-glossary --resume
```

Le relecteur peut corriger ou supprimer des entrées, mais ne peut pas
inventer de termes anglais. Le hachage du prompt et du modèle, le nombre de
tentatives, le mode de relecture et les artefacts sont enregistrés.

### Tableau de bord dans le navigateur

`book-agent ui` lance un tableau de bord accessible uniquement en local
(options `--runs`, `--configs`, `--port`, `--no-browser`). Il couvre les
mêmes points de contrôle que les commandes ci-dessus.

L'interface existe en anglais, chinois simplifié, japonais, français,
espagnol, allemand et coréen, c'est-à-dire les langues pour lesquelles le
pipeline dispose d'un profil. La langue se choisit dans le menu à droite de
l'en-tête ; le choix est conservé dans le navigateur, et l'anglais est la
langue par défaut. La langue de l'interface est indépendante du sens de
traduction d'une tâche (voir
[Localisation du tableau de bord](docs/LOCALIZATION.md), en anglais).

- **Tâches** liste les tâches avec leur sens de traduction (par exemple
  `EN → ZH`) et permet d'en créer ; une tâche terminée dispose d'un bouton
  *Télécharger* pour récupérer le livre traduit, présent aussi dans l'en-tête
  de la tâche, où l'on peut choisir le format (EPUB, Word, HTML, Markdown ou
  texte). L'onglet **Configuration** de chaque tâche sert à la modifier, à la
  valider et à la lancer ; son réglage *Langues* accepte deux codes de langue
  quelconques. **Séries** regroupe les tâches qui partagent un glossaire de
  série versionné. L'en-tête de la tâche permet de mettre en pause (après
  l'appel au modèle en cours), d'arrêter ou de reprendre une exécution. Pour
  une tâche lancée depuis un terminal, `book-agent pause <workspace>` fait de
  même ;
- **Progression** suit n'importe quelle tâche de `--runs` à partir de
  `state.sqlite3` et des journaux de session. Une étape en échec ou en pause
  propose *Reprendre* (le travail terminé est conservé) et *Relancer* ; une
  étape terminée propose *Relancer à partir d'ici*. Une relance
  (`retry --stage X --resume`) indique d'abord quelles étapes seront refaites
  et ce qui sera perdu. La colonne « Dernier changement » indique le dernier
  événement de chaque étape (démarrée, terminée, en échec, arrêtée, en pause,
  en attente de relecture, réinitialisée) et sa date ;
- **Glossaire** permet de modifier et d'approuver un glossaire en attente, ou
  de le confier au relecteur LLM (`approve --glossary` / `--llm-glossary`) ;
- **Texte** présente le livre chapitre par chapitre, source et traduction en
  regard, avec des filtres sur les segments signalés, en file de relecture,
  modifiés ou en conflit. Une fois `validate_repaired` terminée, tout segment
  peut être modifié sur place : l'enregistrement exige un motif et passe par
  le même contrôle déterministe (structure ou marqueurs cassés, texte vide,
  non traduit ou dupliqué, erreurs de ponctuation : bloquants ; les autres
  constats demandent un motif de dérogation). Les modifications sont écrites
  dans `edits/segment-edits.jsonl`, un journal en ajout seul qui n'appartient
  à aucune étape et que les relances n'effacent donc pas ; la compilation
  les applique par-dessus le brouillon validé, et *Recompiler* reconstruit le
  livre lorsque des modifications sont plus récentes que lui. Si une relance
  change la traduction d'un segment modifié, celui-ci passe en conflit et
  bloque la compilation tant que vous n'avez pas choisi entre votre
  modification et le nouveau texte. Chaque modification a un historique et
  peut être annulée. **⤓ Exporter en XLIFF** télécharge le livre au format
  XLIFF 2.1 ; **⤒ Importer un XLIFF** affiche, segment par segment, un aperçu
  du fichier traduit (les unités sont appariées par identifiant, à condition
  que le texte source n'ait pas changé) et n'écrit que ce que vous confirmez,
  sous forme de modifications partageant un même motif (voir
  [Import XLIFF](docs/XLIFF_IMPORT.md), en anglais) ;
- **Relecture finale** traite la file de relecture humaine avec la même
  validation que `resolve-review` ; accepter un segment en l'état exige un
  motif, prédéfini ou libre. Les décisions sont écrites dans le même journal
  que les modifications de l'onglet Texte, et modifier dans l'onglet Texte un
  segment de la file de relecture le traite également. Dès que le nombre de
  segments non tranchés ne dépasse plus
  `workflow.compile_max_unresolved_review_segments`, la page propose
  d'appliquer les décisions prises et d'approuver le brouillon final.
  `book-agent review-ui <workspace>` ouvre directement cette page.

En cas d'erreur inattendue, le tableau de bord affiche un bandeau avec le
message, les détails techniques et un bouton *Recharger*, au lieu d'une page
blanche.

Les modifications peuvent être consultées en ligne de commande, ou exportées
en XLIFF 2.1 :

```powershell
book-agent edits "D:\runs\my-job"
book-agent edits "D:\runs\my-job" --export xliff --output my-job.xlf
```

L'étape `audit_consistency` du pipeline (activée par défaut, sans appel au
modèle) vérifie la cohérence à l'échelle du livre : une phrase ou une
réplique qui revient doit être traduite partout de la même façon, et la
ponctuation doit suivre les conventions propres au livre. Les écarts partent
en correction ; ce que la correction ne règle pas rejoint la file de
relecture. En option, `consistency.style_sheet.enabled: true` extrait aussi,
en même temps que le glossaire, une **feuille de style du livre** :
expressions récurrentes et notes sur chaque personnage. Elle est relue par
une personne au même point de contrôle que le glossaire (section « Feuille de
style » de l'onglet Glossaire, ou `approve --style FILE`), même lorsque le
glossaire est relu par le modèle. Les expressions récurrentes sont traduites
de la même façon dans tout le livre ; les notes sur les personnages ne
servent que de contexte, et ce sont la formulation du texte source et la
scène qui décident des pronoms et du choix entre 你 et 您. Avec
`consistency.story_context.enabled: true`, l'étape facultative
`build_story_context` produit un court résumé par chapitre (environ 6 s
chacun), à partir duquel chaque bloc à traduire reçoit le récit des chapitres
précédents, comme simple contexte et non comme texte à traduire. Voir
[Cohérence à l'échelle du livre](docs/BOOK_CONSISTENCY.md) (en anglais).

Les actions lancées depuis le tableau de bord exécutent la CLI habituelle
dans un processus enfant : les journaux et les points de reprise sont donc
identiques à ceux d'une exécution en terminal, et la tâche se poursuit si
l'on ferme le tableau de bord.

Le tableau de bord est une application React + TypeScript située dans
`frontend/`. Sa version compilée est versionnée dans `book_agent/web/static/`,
si bien que l'installation du paquet ne nécessite pas Node.js. Pour modifier
l'interface (Node 24) :

```powershell
cd frontend
npm ci
npm run dev      # hot-reloading UI; proxies /api to a running `book-agent ui`
npm test         # helper unit tests and component tests (jsdom + Testing Library)
npm run build    # type-check and rebuild book_agent/web/static; commit the result
```

## Codes de sortie

| Code | Signification |
|---:|---|
| `0` | La commande a réussi, ou le traitement s'est terminé normalement |
| `1` | Échec de validation, de configuration, de modèle, d'étape ou d'exécution |
| `2` | Traitement en pause à un point de contrôle de relecture |
| `130` | Annulation par l'utilisateur, ou interruption coopérative de la génération |

## Tests

La suite de tests fonctionne hors ligne par défaut et ne nécessite aucun
modèle local :

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

## Documentation complémentaire

La référence complète des commandes, le détail de chaque option de
configuration et les informations d'exploitation sont pour l'instant
maintenus en anglais :

- [README en anglais](README.md)
- [Guide d'exploitation en production](docs/OPERATIONS.md)
- [Conception](docs/DESIGN.md)
- [Cohérence à l'échelle du livre](docs/BOOK_CONSISTENCY.md)
- [Localisation du tableau de bord](docs/LOCALIZATION.md)
- [Contrôle des étapes dans le tableau de bord : reprise, relance et approbation anticipée](docs/STAGE_CONTROL.md)
- [Relecture du texte intégral et suivi des modifications manuelles](docs/FULL_TEXT_REVIEW.md)
- [Import XLIFF](docs/XLIFF_IMPORT.md)
- [Glossaire de série dans le tableau de bord](docs/SERIES_GLOSSARY_UI.md)
- [Livres aux formats texte, Markdown, HTML et Word](docs/FORMAT_SUPPORT.md)
- [Plan de travail](docs/PLAN.md)
- [Plan de comparaison des frameworks d'inférence (non exécuté)](docs/FRAMEWORK_BENCHMARK_PLAN.md)

## Licence

Le code et la documentation sont distribués sous [licence MIT](LICENSE). Les
EPUB du répertoire `sample/` proviennent du Projet Gutenberg ; ils conservent
leurs propres conditions et ne sont pas couverts par la licence MIT (voir
[sample/README.md](sample/README.md)). Les modèles Ollama ne sont pas
distribués par ce dépôt et restent soumis à leurs licences respectives.

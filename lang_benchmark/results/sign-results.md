# Language-profile benchmarks

## bench-sign-audit10k: English → Simplified Chinese (en-zh)

- **Status**: running; tiers tuned → tuned
- **Skipped checks**: none
- **Cost**: 45 LLM calls, 7.8 min; slowest: audit_translation 5.9 min, repair_translation 1.1 min, review_repaired 0.5 min, repair_review 0.3 min
- **Glossary**: 87 approved entries, pair en-zh; e.g. Abdullah Khan → 阿卜杜拉·汗; Athelney Jones → 阿瑟尼·琼斯; Bartholomew → 巴兹尔·肖尔托; Bouguereau → 布格罗; Brother Bartholomew → 巴托洛缪兄弟; Captain Morstan → 莫斯坦上尉; Corot → 柯罗; Dost Akbar → 多斯特·阿克巴
- **Audit findings** (rules): {}
- **Audit findings** (semantic model): {}
- **Book consistency**: 1 findings; [medium] Repeated quoted line "On the contrary" is rendered "恰恰相反，" h ×1
- **Review queue**: 1 segments
## bench-sign-audit10k

Not evaluated: validated repaired documents are not recorded

## bench-sign-ch1-6-base: English → Simplified Chinese (en-zh)

- **Status**: paused; tiers tuned → tuned
- **Skipped checks**: none
- **Cost**: 45 LLM calls, 7.8 min; slowest: audit_translation 5.9 min, repair_translation 1.1 min, review_repaired 0.5 min, repair_review 0.3 min
- **Glossary**: 87 approved entries, pair en-zh; e.g. Abdullah Khan → 阿卜杜拉·汗; Athelney Jones → 阿瑟尼·琼斯; Bartholomew → 巴兹尔·肖尔托; Bouguereau → 布格罗; Brother Bartholomew → 巴托洛缪兄弟; Captain Morstan → 莫斯坦上尉; Corot → 柯罗; Dost Akbar → 多斯特·阿克巴
- **Audit findings** (rules): {'untranslated': 4, 'glossary': 1}
- **Audit findings** (semantic model): {'mistranslation': 16, 'glossary': 1}
  - untranslated, e.g.: possible untranslated English word: magnifiques | possible untranslated English word: refined | possible untranslated English word: mauvais | possible untranslated English word: hardly
- **Book consistency**: 1 findings; [medium] Repeated quoted line "On the contrary" is rendered "恰恰相反，" h ×1
- **Review queue**: 1 segments
- **Target punctuation** (segments using each form, of 303): {'“ ” quotes': 244, '「」 quotes': 0, '" straight quotes': 0, '—— paired dash': 53, '— single dash': 0}

| source | translation |
|---|---|
| Sherlock Holmes took his bottle from the corner of the mantel-piece and his hypodermic syringe from its neat morocco case. With his long, white, nervous fingers | 夏洛克·福尔摩斯从壁炉架的一角取下他的瓶子，又从精致的摩洛哥皮盒中取出他的皮下注射器。他用那修长、白皙而神经质的手指调整了那根纤细的针头，并卷起左手的衬衫袖口。有一小会儿，他的目光若有所思地停留在那条布满无数针孔和疤痕的筋络分明的前臂和手腕上。最后，他将尖锐的针尖刺入，压下微小的活塞，随即靠回那把天鹅绒衬里的扶手椅，发 |
| Three times a day for many months I had witnessed this performance, but custom had not reconciled my mind to it. On the contrary, from day to day I had become m | 许多个月以来，我一天三次目睹这一场景，但习惯并未使我的心灵对此习以为常。相反，日复一日，我对这一景象愈发烦躁，每当想到自己缺乏抗议的勇气，我的良知便在夜间愈发沉重。我一次又一次立下誓言，要就此事倾诉我的灵魂，但我同伴那种冷静、漫不经人的气度，使他成为我最不愿与之发生任何近乎冒犯之举的人。他那非凡的能力、他大师般的风度， |
| Yet upon that afternoon, whether it was the Beaune which I had taken with my lunch, or the additional exasperation produced by the extreme deliberation of his m | 然而在那个下午，无论是我在午餐时饮用的勃艮第酒，还是他那极端从容的态度所引发的额外恼怒，我突然感到自己再也无法忍受了。 |
| He raised his eyes languidly from the old black-letter volume which he had opened. “It is cocaine,” he said,—“a seven-per-cent. solution. Would you care to try  | 他懒洋洋地从那本打开的古老黑体字书中抬起眼睛。“是可卡因，”他说，“百分之七的溶液。你想试试吗？” |

## bench-sign-ch1-6-story: English → Simplified Chinese (en-zh)

- **Status**: paused; tiers tuned → tuned
- **Skipped checks**: none
- **Cost**: 107 LLM calls, 23.5 min; slowest: audit_translation 12.3 min, translate 5.5 min, extract_glossary 1.6 min, repair_translation 1.2 min
- **Glossary**: 87 approved entries, pair en-zh; e.g. Abdullah Khan → 阿卜杜拉·汗; Athelney Jones → 阿瑟尼·琼斯; Bartholomew → 巴兹尔·肖尔托; Bouguereau → 布格罗; Brother Bartholomew → 巴托洛缪兄弟; Captain Morstan → 莫斯坦上尉; Corot → 柯罗; Dost Akbar → 多斯特·阿克巴
- **Audit findings** (rules): {'untranslated': 3, 'glossary': 3}
- **Audit findings** (semantic model): {'mistranslation': 18, 'glossary': 3, 'untranslated': 1}
  - untranslated, e.g.: possible untranslated English word: magnifiques | The English word 'circumstances' is left untranslated in the target text. | possible untranslated English word: circumstances | possible untranslated English word: mauvais
- **Book consistency**: 1 findings; [medium] Repeated quoted line "On the contrary" is rendered "恰恰相反，" h ×1
- **Review queue**: 2 segments
- **Target punctuation** (segments using each form, of 303): {'“ ” quotes': 245, '「」 quotes': 0, '" straight quotes': 0, '—— paired dash': 54, '— single dash': 0}

| source | translation |
|---|---|
| Sherlock Holmes took his bottle from the corner of the mantel-piece and his hypodermic syringe from its neat morocco case. With his long, white, nervous fingers | 夏洛克·福尔摩斯从壁炉架的一角取下他的瓶子，又从精致的摩洛哥皮盒中取出他的皮下注射器。他用那修长、白皙而神经质的手指调整了那根纤细的针头，然后卷起左手的衬衫袖口。有一小会儿，他的目光若有所思地停留在那条布满无数针孔疤痕的筋络分明的前臂和手腕上。最后，他将尖锐的针头刺入，压下微小的活塞，随即向后靠进那把天鹅绒衬里的扶手椅 |
| Three times a day for many months I had witnessed this performance, but custom had not reconciled my mind to it. On the contrary, from day to day I had become m | 许多个月以来，我一天三次目睹这一场景，但习惯并未使我的心灵与之和解。相反，日复一日，我对这一景象愈发烦躁，每当想到自己缺乏抗议的勇气，我的良知便在夜间愈发沉重。我一次又一次立下誓言，要就此事倾诉我的灵魂，但我同伴那种冷静、漫不经人的气度，使他成为我最不愿与之发生任何近乎冒犯之举的人。他那非凡的能力、他大师般的风度，以及 |
| Yet upon that afternoon, whether it was the Beaune which I had taken with my lunch, or the additional exasperation produced by the extreme deliberation of his m | 然而在那个下午，无论是我在午餐时饮下的勃艮第酒，还是他那极端从容的态度所引发的额外恼怒，我突然感到自己再也无法忍受了。 |
| He raised his eyes languidly from the old black-letter volume which he had opened. “It is cocaine,” he said,—“a seven-per-cent. solution. Would you care to try  | 他懒洋洋地从那本打开的旧黑体字书中抬起眼睛。“是可卡因，”他说，“百分之七的溶液。你想试试吗？” |

## bench-sign-tg-en-de: English → German (en>de)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: untranslated text, character report, prose rewrite
- **Cost**: 496 LLM calls, 139.9 min; slowest: audit_translation 101.2 min, translate 15.9 min, repair_translation 13.8 min, extract_glossary 3.4 min
- **Glossary**: 130 approved entries, pair en>de; e.g. Abdullah Khan → Abdullah Khan; Arthur Morstan → Arthur Morstan; Athelney Jones → Athelney Jones; Bartholomew → Bartholomew; Bartholomew Sholto → Bartholomew Sholto; Bouguereau → Bouguereau; Brother Bartholomew → Bruder Bartholomew; Captain Morstan → Captain Morstan
- **Style sheet characters**: Athelney Jones (er, Sie); Brother Bartholomew (er, Sie); Captain Morstan (er, Sie); Dr. Watson (er, Sie); François Le Villard (er, Sie); Gregson (er, Sie)
- **Audit findings** (rules): {'glossary': 20, 'mistranslation': 4}
- **Audit findings** (semantic model): {'mistranslation': 80, 'omission': 9, 'glossary': 7, 'untranslated': 3, 'structure': 1}
  - untranslated, e.g.: The phrase 'Very sorry' is translated as 'Sehr leid', which is a literal word-for-word tra | The translation replaces the specific metaphor 'missing links' (referring to a chain of ev | The translation fails to preserve the dash (em-dash) used in the source to indicate a brea
  - numbers, e.g.: numeric content differs from source: SOURCE: "three little children" (quantity: 3, object: | numeric content differs from source: The translation contains significant added content (h | numeric content differs from source: SOURCE: "not one inch" (unit: inch); TRANSLATION: "ke
- **Book consistency**: no findings
- **Review queue**: 4 segments
- **Target punctuation** (segments using each form, of 303): {'„ “ quotes': 244, '» « quotes': 0, '” English closing quote': 0, '" straight quotes': 0, '– Gedankenstrich': 16}

| source | translation |
|---|---|
| Sherlock Holmes took his bottle from the corner of the mantel-piece and his hypodermic syringe from its neat morocco case. With his long, white, nervous fingers | Sherlock Holmes nahm seine Flasche vom Kaminsims und seine Hypodermespritzspritze aus ihrer ordentlichen Marokkoeinlage. Mit seinen langen, weißen, nervösen Fin |
| Three times a day for many months I had witnessed this performance, but custom had not reconciled my mind to it. On the contrary, from day to day I had become m | Dreimal täglich über viele Monate hatte ich diese Szene beobachtet, aber die Gewohnheit hatte meinen Geist nicht damit versöhnt. Im Gegenteil, von Tag zu Tag wa |
| Yet upon that afternoon, whether it was the Beaune which I had taken with my lunch, or the additional exasperation produced by the extreme deliberation of his m | Doch an diesem Nachmittag, ob es nun der Beaune war, den ich zu meinem Mittagessen getrunken hatte, oder die zusätzliche Verärgerung, die durch die extreme Bedä |
| He raised his eyes languidly from the old black-letter volume which he had opened. “It is cocaine,” he said,—“a seven-per-cent. solution. Would you care to try  | Er hob seine Augen träge von dem alten Frakturband, das er aufgeschlagen hatte. „Es ist Kokain“, sagte er, „eine siebenprozentige Lösung. Möchten Sie es versuch |

## bench-sign-tg-en-fr: English → French (en>fr)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: untranslated text, character report, prose rewrite
- **Cost**: 456 LLM calls, 66.5 min; slowest: audit_translation 36.4 min, translate 16.9 min, repair_translation 7.5 min, extract_glossary 2.0 min
- **Glossary**: 80 approved entries, pair en>fr; e.g. Abdullah Khan → Abdullah Khan; Arthur Morstan → Arthur Morstan; Athelney Jones → Athelney Jones; Bartholomew → Bartholomew; Bouguereau → Bouguereau; Brother Bartholomew → Frère Bartholomew; Captain Morstan → Captain Morstan; Corot → Corot
- **Style sheet characters**: Athelney Jones (il, vous); Brother Bartholomew (il, vous); Captain Morstan (il, vous); Dr. Watson (il, vous); Hindoo servant (il, vous); Lal Chowdar (il, vous); Major Sholto (il, vous); McMurdo (il, vous)
- **Audit findings** (rules): {'glossary': 30, 'mistranslation': 2}
- **Audit findings** (semantic model): {'mistranslation': 66, 'omission': 17, 'untranslated': 4, 'structure': 1, 'glossary': 1, 'punctuation': 1}
  - untranslated, e.g.: The translation adds a specific spatial referent ('ces murs') not found in the source, tur | Unsupported addition: The translation introduces a gesture and a desire for silence that d | The translation is missing the closing quotation mark at the end of the segment. | The translation introduces an unsupported cognitive action ('vous rendre compte que') not 
  - numbers, e.g.: numeric content differs from source: In the source, 'third-rate' is an adjective describin | numeric content differs from source: The source height of the building is 'seventy-four fe
- **Book consistency**: 13 findings; [low] The style sheet renders "The sign of the four" as "Le Signe  ×3; [low] The style sheet renders "Mr. Sholto" as "Monsieur Sholto", b ×3; [low] The style sheet renders "Brother Bartholomew" as "Frère Bart ×2; [low] The style sheet renders "Mr. Thaddeus" as "Monsieur Thaddeus ×2; [low] The style sheet renders "Number One" as "le Numéro Un", but  ×1
- **Review queue**: 2 segments
- **Target punctuation** (segments using each form, of 303): {'« » quotes': 244, '" straight quotes': 0, '“ ” quotes': 0, 'no-break space before ; : ! ?': 0, 'no such space before ; : ! ?': 109}

| source | translation |
|---|---|
| Sherlock Holmes took his bottle from the corner of the mantel-piece and his hypodermic syringe from its neat morocco case. With his long, white, nervous fingers | Sherlock Holmes prit sa bouteille dans le coin de la cheminée et sa seringue hypodermique dans son étui en cuir soigné. Avec ses longs doigts blancs et nerveux, |
| Three times a day for many months I had witnessed this performance, but custom had not reconciled my mind to it. On the contrary, from day to day I had become m | Pendant de nombreux mois, j’avais été témoin de cette scène trois fois par jour, mais l’habitude n’avait pas apaisé mon esprit. Au contraire, de jour en jour, j |
| Yet upon that afternoon, whether it was the Beaune which I had taken with my lunch, or the additional exasperation produced by the extreme deliberation of his m | Pourtant, cet après-midi-là, que ce soit à cause du Beaune que j’avais pris avec mon déjeuner, ou de l’exaspération supplémentaire causée par l’extrême lenteur  |
| He raised his eyes languidly from the old black-letter volume which he had opened. “It is cocaine,” he said,—“a seven-per-cent. solution. Would you care to try  | Il leva ses yeux, d’un air las, du vieux volume à reliure noire qu’il avait ouvert. « C’est de la cocaïne », dit-il, « une solution à sept pour cent. Voulez-vou |


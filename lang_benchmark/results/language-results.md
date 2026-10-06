# Language-profile benchmarks

## bench-lang-ja-zh: Japanese → Simplified Chinese (ja>zh)

- **Status**: complete; tiers profiled → tuned
- **Skipped checks**: untranslated text
- **Cost**: 6 LLM calls, 2.4 min; slowest: translate 1.0 min, audit_translation 0.6 min, extract_glossary 0.5 min, resolve_glossary 0.3 min
- **Glossary**: 15 approved entries, pair ja>zh; e.g. 芥川龍之介 → 芥川龙之介; 京都 → 京都; 朱雀大路 → 朱雀大路; 洛中 → 洛中; 羅生門 → 罗生门; 検非違使 → 检非违使; 太刀帯 → 太刀带; 市女笠 → 市女笠
- **Style sheet characters**: 下人 (他, 你)
- **Audit findings** (rules): {}
- **Audit findings** (semantic model): {}
- **Book consistency**: no findings
- **Review queue**: 0 segments
- **Target punctuation** (segments using each form, of 18): {'“ ” quotes': 1, '「」 quotes': 0, '" straight quotes': 0, '—— paired dash': 1, '— single dash': 0}

| source | translation |
|---|---|
| This eBook is for the use of anyone anywhere in the United States and most other parts of the world at no cost and with almost no restrictions whatsoever. You m | 本电子书供美国及世界其他大部分地区任何人免费使用，几乎不受任何限制。您可以根据随附于本电子书或在线发布于www.gutenberg.org的古腾堡计划许可证条款，复制、赠送或重新使用它。如果您不在美国境内，在使用本电子书之前，必须检查您所在国家的法律。 |
| Release date: November 1, 1999 [eBook #1982] Most recently updated: April 15, 2013 | 发布日期：1999年11月1日 [电子书 #1982] 最近更新时间：2013年4月15日 |
| This file is encoded in Japanese. Your computer must be Japanese-capable to read it. | 此文件以日语编码。您的计算机必须具备日语处理能力才能阅读。 |
| The text was taken from a 1917 edition which is naturally written in the traditional orthography with prewar kanji forms. I have taken the liberty of using post | 文本取自1917年版，自然采用战前汉字形式的传统正字法。我擅自使用了战后正字法和汉字形式，并在认为必要时添加了括号内的读音。 |

## bench-lang-en-fr: English → French (en>fr)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: untranslated text, character report, prose rewrite
- **Cost**: 74 LLM calls, 22.7 min; slowest: audit_translation 14.7 min, translate 3.8 min, extract_glossary 1.4 min, repair_translation 0.7 min
- **Glossary**: 43 approved entries, pair en>fr; e.g. Ada → Ada; Alice → Alice; Bill → Bill; Canary → Canari; Crab → Crabe; Dinah → Dinah; Dodo → Dodo; Duchess → Duchesse
- **Style sheet characters**: Alice (elle, vous); Bill (il, vous); Crab (il, vous); Dinah (elle, tu); Dodo (il, vous); Duck (il, vous); Eaglet (il, vous); Fury (il, vous)
- **Audit findings** (rules): {'glossary': 1, 'untranslated': 1}
- **Audit findings** (semantic model): {'mistranslation': 8, 'structure': 1}
  - untranslated, e.g.: translation is identical to source
- **Book consistency**: 5 findings; [low] The style sheet renders "yer honour" as "votre honneur", but ×4; [medium] English quotation marks “ ” are used here, but the book uses ×1
- **Review queue**: 2 segments
- **Target punctuation** (segments using each form, of 145): {'« » quotes': 114, '" straight quotes': 0, '“ ” quotes': 1, 'no-break space before ; : ! ?': 0, 'no such space before ; : ! ?': 121}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | Alice commençait à s’ennuyer à force de rester assise sur la rive auprès de sa sœur, sans rien avoir à faire : une ou deux fois, elle avait jeté un coup d’œil a |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | Elle se demandait donc dans sa tête (autant qu’elle le pouvait, car la chaleur du jour la rendait fort somnolente et stupide) si le plaisir de faire une couronn |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | Il n’y avait rien de très remarquable là-dedans ; ni Alice ne trouva-t-elle très étrange d’entendre le Lapin se dire à lui-même : « Oh, mon Dieu ! Oh, mon Dieu  |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | Un instant après, Alice s’y précipitait à sa suite, sans jamais songer une seule fois à la manière dont elle pourrait en sortir. |

## bench-lang-en-ja: English → Japanese (en>ja)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 62 LLM calls, 21.0 min; slowest: approve_glossary 8.7 min, audit_translation 4.8 min, translate 4.1 min, extract_glossary 1.2 min
- **Glossary**: 41 approved entries, pair en>ja; e.g. Ada → アダ; Alice → アリス; Bill → ビル; caterpillar → 毛虫; Dinah → ダイナ; Dodo → ドードー; Duchess → 公爵夫人; Duck → アヒル
- **Style sheet characters**: Alice (彼女, —); Bill (彼, —); caterpillar (彼, —); Dinah (彼女, —); Dodo (彼, —); Duck (彼, —); Eaglet (彼, —); Lory (彼, —)
- **Audit findings** (rules): {'duplication': 1, 'untranslated': 1}
- **Audit findings** (semantic model): {'mistranslation': 8}
  - untranslated, e.g.: possible untranslated English word: splash
- **Book consistency**: 1 findings; [low] Repeated quoted line "I beg your pardon" is rendered "失礼しました ×1
- **Review queue**: 1 segments
- **Target punctuation** (segments using each form, of 145): {'「」 quotes': 114, '" or “ ” quotes': 0, '…… ellipsis': 0, '… or ... ellipsis': 0}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | アリスは、姉のそばで川べりに座り、何をするでもなく過ごしているうちに、次第にうんざりしてきた。姉が読んでいる本をのぞいてみたが、絵も会話もない。「絵も会話もない本に、いったい何の意味があるのかしら」とアリスは思った。 |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | そこで彼女は、自分の頭の中で（暑い日差しで眠く、ぼんやりしていたが、できる限りの）考えを巡らせていた。タンポポの冠を作る楽しみは、起きてタンポポを摘む手間に見合うものだろうか、と。そのとき、突然、ピンク色の目をした白ウサギが、彼女のすぐそばを駆け抜けていった。 |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | それ自体は、別にたいそう珍しいことではなかった。ウサギが独り言で「あらまあ、あらまあ、遅れるわ！」と言っているのを聞くのも、アリスにはたいそうおかしいことには思えなかった（後で思い返すと、そこで驚くべきだったのに、と気づいたが、当時はすべてごく自然に思えた）。しかし、ウサギが実際にベストのポケットから懐中時計を取り出し |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | 次の瞬間、アリスはウサギの後を追って落ちていった。どうやってまた出ていくのか、という考えは一度も頭をよぎらなかった。 |

## bench-lang-fr-en: French → English (fr>en)

- **Status**: paused; tiers profiled → tuned
- **Skipped checks**: untranslated text, punctuation conventions, character report
- **Cost**: 90 LLM calls, 16.6 min; slowest: audit_translation 5.1 min, translate 4.5 min, extract_glossary 2.7 min, resolve_glossary 1.4 min
- **Glossary**: 99 approved entries, pair fr>en; e.g. Arcadia Walker → Arcadia Walker; Dean Forsyth → Dean Forsyth; Flora Hudelson → Flora Hudelson; Francis Gordon → Francis Gordon; Jenny → Jenny; Jenny Hudelson → Jenny Hudelson; John Proth → John Proth; Kate → Kate
- **Style sheet characters**: Arcadia Walker (she, —); Dean Forsyth (he, —); Flora Hudelson (she, —); Francis Gordon (he, —); Jenny (she, —); John Proth (he, —); Kate (she, —); Loo (she, —)
- **Audit findings** (rules): {'glossary': 15, 'untranslated': 2}
- **Audit findings** (semantic model): {'mistranslation': 19}
  - untranslated, e.g.: first-draft translation retries exhausted; source text was retained for downstream repair | first-draft translation retries exhausted; source text was retained for downstream repair
- **Book consistency**: 10 findings; [low] The style sheet renders "rendez-vous" as "rendezvous", but t ×7; [low] The style sheet renders "Mon oncle" as "My uncle", but this  ×3
- **Review queue**: 1 segments
- **Target punctuation** (segments using each form, of 340): {'“ ” quotes': 90, '" straight quotes': 0}

| source | translation |
|---|---|
| I DANS LEQUEL LE JUGE JOHN PROTH REMPLIT UN DES PLUS AGRÉABLES DEVOIRS DE SA CHARGE AVANT DE RETOURNER A SON JARDIN. | I IN WHICH JUDGE JOHN PROTH PERFORMS ONE OF THE MOST PLEASANT DUTIES OF HIS OFFICE BEFORE RETURNING TO HIS GARDEN. |
| Il n’y a aucun motif pour cacher aux lecteurs que la ville dans laquelle commence cette histoire singulière est située en Virginie, États-Unis d’Amérique. S’ils | There is no reason to conceal from the readers that the city in which this singular story begins is located in Virginia, United States of America. If they are s |
| Cette année-là, le 12 mars, dans la matinée, ceux des habitants de Whaston qui traversèrent Exeter street au moment convenable purent apercevoir un élégant cava | That year, on March 12, in the morning, those inhabitants of Whaston who crossed Exeter Street at the opportune moment were able to see an elegant rider mount a |
| Ce cavalier, de pur type yankee, type qui n’est point exempt d’une originale distinction, ne devait pas avoir plus de trente ans. Il était d’une taille au-dessu | This rider, of pure Yankee type, a type not devoid of a certain original distinction, could not have been more than thirty years old. He was of above-average he |


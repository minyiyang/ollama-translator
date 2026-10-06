# Language-profile benchmarks

## bench-zhsrc-qwen-zh-fr: Simplified Chinese → French (zh>fr)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 144 LLM calls, 19.9 min; slowest: audit_translation 6.7 min, translate 3.4 min, extract_glossary 3.0 min, repair_translation 2.4 min
- **Glossary**: 126 approved entries, pair zh>fr; e.g. 假洋鬼子 → le faux fantôme étranger; 吴妈 → la servante Wu; 地保 → le gardien du village; 小D → le petit D; 小尼姑 → la jeune nonne; 王癞胡 → Wang Laihu; 王胡 → Wang Hu; 秀才 → le xiucai
- **Style sheet characters**: 假洋鬼子 (il, vous); 吴妈 (elle, vous); 地保 (il, vous); 小D (il, vous); 小尼姑 (elle, vous); 王胡 (il, tu); 秀才 (il, vous); 老尼姑 (elle, vous)
- **Audit findings** (rules): {'glossary': 56, 'mistranslation': 3, 'punctuation': 1, 'duplication': 1}
- **Audit findings** (semantic model): {'mistranslation': 19, 'glossary': 4, 'structure': 3}
  - numbers, e.g.: numeric content differs from source: In SOURCE, '列传' (lièzhuàn) refers to 'biographies in  | numeric content differs from source: In SOURCE, '四五个响头' (four or five kowtows/head knocks) | numeric content differs from source: The source describes the impossibility of finding sui
- **Book consistency**: 3 findings; [low] The style sheet renders "心满意足的得胜的走了" as "partit victorieux,  ×2; [low] The style sheet renders "你算是什么东西" as "Qu'est-ce que tu es, t ×1
- **Review queue**: 16 segments
- **Target punctuation** (segments using each form, of 163): {'« » quotes': 90, '" straight quotes': 0, '“ ” quotes': 0, 'no-break space before ; : ! ?': 0, 'no such space before ; : ! ?': 101}

| source | translation |
|---|---|
| 我要给阿Q做正传，已经不止一两年了。但一面要做，一面又往回想，这足见我不是一个『立言』的人，因为从来不朽之笔，须传不朽之人，于是人以文传，文以人传——究竟谁靠谁传，渐渐的不甚了然起来，而终于归结到传阿Q，仿佛思想里有鬼似的。 | J'ai l'intention d'écrire la véritable histoire d'Ah Q depuis plus d'un an ou deux. Mais à mesure que je m'y mets, je me retourne vers le passé, ce qui prouve q |
| 然而要做这一篇速朽的文章，才下笔，便感到万分的困难了。第一是文章的名目。孔子曰：『名不正则言不顺。』这原是应该极注意的。传的名目很繁多：列传、自传、内传、外传、别传、家传、小传，……而可惜都不合。『列传』么，这一篇并非和许多阔人排在『正史』里；『自传』么，我又并非就是阿Q。说是『外传』，『内传』在那里呢？倘用『内传』， | Pourtant, à peine ai-je posé la plume pour rédiger cet article destiné à périr vite, que je ressens une difficulté extrême. D'abord, le titre de l'article. Conf |
| 第二，立传的通例，开首大抵该是『某，字某，某地人也』，而我并不知道阿Q姓什么。有一回，他似乎是姓赵，但第二日便模糊了。那是赵太爷的儿子进了秀才的时候，锣声铛铛的报到村里来，阿Q正喝了两碗黄酒，便手舞足蹈的说，这于他也很光采，因为他和赵太爷原来是本家，细细的排起来他还比秀才长三辈呢。其时几个旁听人倒也肃然的有些起敬了。那 | Deuxièmement, selon la convention des biographies, le début devrait généralement être « Tel, surnommé Tel, est de la région de Tel », mais je ne sais pas quel e |
| 阿Q并没有抗辩他确凿姓赵，只用手摸着左颊，和地保退出去了；外面又被地保训斥了一番，谢了地保二百文酒钱。知道的人都说阿Q太荒唐，自己去招打；他大约未必姓赵，即使真姓赵，有赵太爷在这里，也不该如此胡说的。此后便再没有人提起他的氏族来，所以我终于不知道阿Q究竟什么姓。 | Ah Q n'a pas contesté qu'il était vraiment de la famille Zhao, il s'est seulement touché la joue gauche de la main et est sorti avec le gardien du village ; deh |

## bench-zhsrc-tg-zh-fr: Simplified Chinese → French (zh>fr)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 186 LLM calls, 41.5 min; slowest: translate 18.6 min, audit_translation 8.1 min, repair_translation 5.7 min, extract_glossary 3.0 min
- **Glossary**: 126 approved entries, pair zh>fr; e.g. 假洋鬼子 → le faux fantôme étranger; 吴妈 → la servante Wu; 地保 → le gardien du village; 小D → le petit D; 小尼姑 → la jeune nonne; 王癞胡 → Wang Laihu; 王胡 → Wang Hu; 秀才 → le xiucai
- **Style sheet characters**: 假洋鬼子 (il, vous); 吴妈 (elle, vous); 地保 (il, vous); 小D (il, vous); 小尼姑 (elle, vous); 王胡 (il, tu); 秀才 (il, vous); 老尼姑 (elle, vous)
- **Audit findings** (rules): {'glossary': 85, 'mistranslation': 6, 'punctuation': 1, 'duplication': 1}
- **Audit findings** (semantic model): {'mistranslation': 16, 'glossary': 6, 'omission': 3, 'untranslated': 2}
  - untranslated, e.g.: The translation omits the specific phrasing '深恶而痛绝之', which is highlighted in the source w | The translation adds 'de se promener', turning a state of being (bare-chested) into a spec
  - numbers, e.g.: numeric content differs from source: In SOURCE, the quantity '四五个' (four or five) measures | numeric content differs from source: SOURCE: '母亲大哭了十几场' (mother cried 'ten-plus times/sess | numeric content differs from source: The translation changes the polarity/condition of the
- **Book consistency**: 4 findings; [low] The style sheet renders "你算是什么东西" as "Qu'est-ce que tu es, t ×1; [medium] A straight quotation mark " is used here, but the book uses  ×1; [low] The style sheet renders "飘飘然的似乎要飞去了" as "semblait flotter, c ×1; [low] The style sheet renders "女人，女人！" as "Femme, femme !", but th ×1
- **Review queue**: 22 segments
- **Target punctuation** (segments using each form, of 163): {'« » quotes': 87, '" straight quotes': 1, '“ ” quotes': 0, 'no-break space before ; : ! ?': 0, 'no such space before ; : ! ?': 93}

| source | translation |
|---|---|
| 我要给阿Q做正传，已经不止一两年了。但一面要做，一面又往回想，这足见我不是一个『立言』的人，因为从来不朽之笔，须传不朽之人，于是人以文传，文以人传——究竟谁靠谁传，渐渐的不甚了然起来，而终于归结到传阿Q，仿佛思想里有鬼似的。 | J'ai l'intention d'écrire la Véritable Histoire d'Ah Q depuis plus d'un ou deux ans. Mais à mesure que je m'y mets, je me retourne vers le passé, ce qui prouve  |
| 然而要做这一篇速朽的文章，才下笔，便感到万分的困难了。第一是文章的名目。孔子曰：『名不正则言不顺。』这原是应该极注意的。传的名目很繁多：列传、自传、内传、外传、别传、家传、小传，……而可惜都不合。『列传』么，这一篇并非和许多阔人排在『正史』里；『自传』么，我又并非就是阿Q。说是『外传』，『内传』在那里呢？倘用『内传』， | Pourtant, à peine ai-je posé le pinceau pour rédiger cet article destiné à périr vite, que je ressens une difficulté extrême. D'abord, le titre. Confucius dit : |
| 第二，立传的通例，开首大抵该是『某，字某，某地人也』，而我并不知道阿Q姓什么。有一回，他似乎是姓赵，但第二日便模糊了。那是赵太爷的儿子进了秀才的时候，锣声铛铛的报到村里来，阿Q正喝了两碗黄酒，便手舞足蹈的说，这于他也很光采，因为他和赵太爷原来是本家，细细的排起来他还比秀才长三辈呢。其时几个旁听人倒也肃然的有些起敬了。那 | Deuxièmement, la convention veut que la biographie commence par « Tel, surnommé Tel, originaire de Tel lieu », mais je ne sais pas le nom de famille d'Ah Q. Un  |
| 阿Q并没有抗辩他确凿姓赵，只用手摸着左颊，和地保退出去了；外面又被地保训斥了一番，谢了地保二百文酒钱。知道的人都说阿Q太荒唐，自己去招打；他大约未必姓赵，即使真姓赵，有赵太爷在这里，也不该如此胡说的。此后便再没有人提起他的氏族来，所以我终于不知道阿Q究竟什么姓。 | Ah Q ne se défendit pas en affirmant qu'il était bien de la famille Zhao ; il se toucha la joue gauche de la main et sortit avec le gardien du village. Dehors,  |

## bench-zhsrc-qwen-zh-de: Simplified Chinese → German (zh>de)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 116 LLM calls, 20.0 min; slowest: audit_translation 7.7 min, translate 3.5 min, extract_glossary 2.5 min, repair_translation 2.2 min
- **Glossary**: 98 approved entries, pair zh>de; e.g. 假洋鬼子 → Falscher Ausländer; 吴妈 → Mutter Wu; 小D → Kleiner D; 小Don → Kleiner Don; 小尼姑 → die kleine Nonne; 王癞胡 → Wang Laih; 王胡 → Wang Hu; 老尼姑 → die alte Nonne
- **Style sheet characters**: 假洋鬼子 (er, Sie); 吴妈 (sie, Sie); 地保 (er, Sie); 小D (er, du); 小尼姑 (sie, du); 王胡 (er, du); 秀才 (er, Sie); 老尼姑 (sie, du)
- **Audit findings** (rules): {'glossary': 29, 'mistranslation': 4, 'duplication': 1}
- **Audit findings** (semantic model): {'mistranslation': 11, 'glossary': 3}
  - numbers, e.g.: numeric content differs from source: In the source, the phrase '取出『正传』两个字来' refers to extr | numeric content differs from source: In the SOURCE, the onion leaves added to the fish are | numeric content differs from source: The translation changes the subject of the beating fr
- **Book consistency**: 3 findings; [low] The style sheet renders "心满意足的得胜的走了" as "zufrieden und siegr ×2; [low] The style sheet renders "飘飘然的似乎要飞去了" as "schwebend, als würd ×1
- **Review queue**: 14 segments
- **Target punctuation** (segments using each form, of 163): {'„ “ quotes': 84, '» « quotes': 0, '” English closing quote': 0, '" straight quotes': 0, '– Gedankenstrich': 16}

| source | translation |
|---|---|
| 我要给阿Q做正传，已经不止一两年了。但一面要做，一面又往回想，这足见我不是一个『立言』的人，因为从来不朽之笔，须传不朽之人，于是人以文传，文以人传——究竟谁靠谁传，渐渐的不甚了然起来，而终于归结到传阿Q，仿佛思想里有鬼似的。 | Ich beabsichtige, eine Biographie über A-Q zu verfassen, seit nunmehr mehr als ein oder zwei Jahren. Doch während ich schreibe, blicke ich zugleich zurück; dies |
| 然而要做这一篇速朽的文章，才下笔，便感到万分的困难了。第一是文章的名目。孔子曰：『名不正则言不顺。』这原是应该极注意的。传的名目很繁多：列传、自传、内传、外传、别传、家传、小传，……而可惜都不合。『列传』么，这一篇并非和许多阔人排在『正史』里；『自传』么，我又并非就是阿Q。说是『外传』，『内传』在那里呢？倘用『内传』， | Doch als ich mich an dieses schnell vergängliche Werk machte, spürte ich, kaum hatte ich die Feder in die Hand genommen, eine unermessliche Schwierigkeit. Erste |
| 第二，立传的通例，开首大抵该是『某，字某，某地人也』，而我并不知道阿Q姓什么。有一回，他似乎是姓赵，但第二日便模糊了。那是赵太爷的儿子进了秀才的时候，锣声铛铛的报到村里来，阿Q正喝了两碗黄酒，便手舞足蹈的说，这于他也很光采，因为他和赵太爷原来是本家，细细的排起来他还比秀才长三辈呢。其时几个旁听人倒也肃然的有些起敬了。那 | Zweitens: Die übliche Konvention bei Biographien beginnt meist mit „So und so, Beiname So und so, aus dem Ort So und so“, doch ich weiß nicht, welchen Nachnamen |
| 阿Q并没有抗辩他确凿姓赵，只用手摸着左颊，和地保退出去了；外面又被地保训斥了一番，谢了地保二百文酒钱。知道的人都说阿Q太荒唐，自己去招打；他大约未必姓赵，即使真姓赵，有赵太爷在这里，也不该如此胡说的。此后便再没有人提起他的氏族来，所以我终于不知道阿Q究竟什么姓。 | A-Q widersprach nicht, dass er tatsächlich den Nachnamen Zhao trug; er berührte nur mit der Hand seine linke Wange und ging mit dem Dibao hinaus. Draußen wurde  |

## bench-zhsrc-tg-zh-de: Simplified Chinese → German (zh>de)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 193 LLM calls, 46.1 min; slowest: translate 18.4 min, audit_translation 10.4 min, repair_translation 9.6 min, extract_glossary 2.7 min
- **Glossary**: 98 approved entries, pair zh>de; e.g. 假洋鬼子 → Falscher Ausländer; 吴妈 → Mutter Wu; 小D → Kleiner D; 小Don → Kleiner Don; 小尼姑 → die kleine Nonne; 王癞胡 → Wang Laih; 王胡 → Wang Hu; 老尼姑 → die alte Nonne
- **Style sheet characters**: 假洋鬼子 (er, Sie); 吴妈 (sie, Sie); 地保 (er, Sie); 小D (er, du); 小尼姑 (sie, du); 王胡 (er, du); 秀才 (er, Sie); 老尼姑 (sie, du)
- **Audit findings** (rules): {'glossary': 104, 'mistranslation': 9, 'omission': 6, 'addition': 4, 'duplication': 2}
- **Audit findings** (semantic model): {'mistranslation': 21, 'glossary': 7, 'omission': 4}
  - numbers, e.g.: numeric content differs from source: In the SOURCE, the quantity '长三辈' (three generations  | numeric content differs from source: In the SOURCE, the name options are '阿桂' (A-Gui) and  | numeric content differs from source: The translation changes the quantity of the onion lea
- **Book consistency**: 4 findings; [low] The style sheet renders "心满意足的得胜的走了" as "zufrieden und siegr ×2; [low] The style sheet renders "飘飘然的似乎要飞去了" as "schwebend, als würd ×2
- **Review queue**: 21 segments
- **Target punctuation** (segments using each form, of 163): {'„ “ quotes': 83, '» « quotes': 0, '” English closing quote': 0, '" straight quotes': 0, '– Gedankenstrich': 10}

| source | translation |
|---|---|
| 我要给阿Q做正传，已经不止一两年了。但一面要做，一面又往回想，这足见我不是一个『立言』的人，因为从来不朽之笔，须传不朽之人，于是人以文传，文以人传——究竟谁靠谁传，渐渐的不甚了然起来，而终于归结到传阿Q，仿佛思想里有鬼似的。 | Ich wollte schon seit über einem Jahr eine Biographie über A-Q schreiben. Aber während ich daran arbeitete, dachte ich immer wieder darüber nach, und ich merkte |
| 然而要做这一篇速朽的文章，才下笔，便感到万分的困难了。第一是文章的名目。孔子曰：『名不正则言不顺。』这原是应该极注意的。传的名目很繁多：列传、自传、内传、外传、别传、家传、小传，……而可惜都不合。『列传』么，这一篇并非和许多阔人排在『正史』里；『自传』么，我又并非就是阿Q。说是『外传』，『内传』在那里呢？倘用『内传』， | Allerdings ist es schwierig, diesen schnell vergänglichen Artikel zu schreiben. Sobald ich anfange, stelle ich fest, dass es viele Schwierigkeiten gibt. Erstens |
| 第二，立传的通例，开首大抵该是『某，字某，某地人也』，而我并不知道阿Q姓什么。有一回，他似乎是姓赵，但第二日便模糊了。那是赵太爷的儿子进了秀才的时候，锣声铛铛的报到村里来，阿Q正喝了两碗黄酒，便手舞足蹈的说，这于他也很光采，因为他和赵太爷原来是本家，细细的排起来他还比秀才长三辈呢。其时几个旁听人倒也肃然的有些起敬了。那 | Zweitens ist es die übliche Form, eine Biographie zu beginnen, nämlich „So-und-so, mit dem Namen So-und-so, ist eine Person aus So-und-so“. Aber ich weiß nicht, |
| 阿Q并没有抗辩他确凿姓赵，只用手摸着左颊，和地保退出去了；外面又被地保训斥了一番，谢了地保二百文酒钱。知道的人都说阿Q太荒唐，自己去招打；他大约未必姓赵，即使真姓赵，有赵太爷在这里，也不该如此胡说的。此后便再没有人提起他的氏族来，所以我终于不知道阿Q究竟什么姓。 | A-Q widersprach nicht, dass er tatsächlich den Nachnamen Zhao trage, sondern strich sich nur mit der Hand über die linke Wange und trat mit dem Dibao den Raum;  |

## bench-zhsrc-qwen-zh-es: Simplified Chinese → Spanish (zh>es)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 120 LLM calls, 20.6 min; slowest: audit_translation 8.7 min, translate 3.3 min, extract_glossary 3.1 min, repair_translation 2.1 min
- **Glossary**: 114 approved entries, pair zh>es; e.g. 假洋鬼子 → el falso extranjero; 吴妈 → la sirvienta Wu; 地保 → el alguacil; 妲己 → Daji; 小D → el joven D; 小尼姑 → la joven monja; 少奶奶 → la joven señora; 王癞胡 → Wang Laihu
- **Style sheet characters**: 假洋鬼子 (él, tú); 吴妈 (ella, tú); 地保 (él, tú); 小D (él, tú); 小尼姑 (ella, tú); 少奶奶 (ella, tú); 王胡 (él, tú); 秀才 (él, tú)
- **Audit findings** (rules): {'glossary': 29, 'mistranslation': 2, 'duplication': 1}
- **Audit findings** (semantic model): {'mistranslation': 15, 'glossary': 1}
  - numbers, e.g.: numeric content differs from source: In SOURCE, '四五个' (four or five) measures the quantity | numeric content differs from source: The source specifies the mother cried '十几场' (more tha
- **Book consistency**: 1 findings; [medium] An exclamation here has no opening ¡, but the book writes th ×1
- **Review queue**: 9 segments
- **Target punctuation** (segments using each form, of 163): {'« » quotes': 82, '“ ” quotes': 0, '" straight quotes': 0, '¿ opening': 26, '? without ¿ in the segment': 1, '— raya dialogue': 15}

| source | translation |
|---|---|
| 我要给阿Q做正传，已经不止一两年了。但一面要做，一面又往回想，这足见我不是一个『立言』的人，因为从来不朽之笔，须传不朽之人，于是人以文传，文以人传——究竟谁靠谁传，渐渐的不甚了然起来，而终于归结到传阿Q，仿佛思想里有鬼似的。 | Ya hace más de un año que intento escribir la biografía legítima de A-Q. Pero mientras escribo, me vuelvo a mirar hacia atrás, lo cual demuestra que no soy un h |
| 然而要做这一篇速朽的文章，才下笔，便感到万分的困难了。第一是文章的名目。孔子曰：『名不正则言不顺。』这原是应该极注意的。传的名目很繁多：列传、自传、内传、外传、别传、家传、小传，……而可惜都不合。『列传』么，这一篇并非和许多阔人排在『正史』里；『自传』么，我又并非就是阿Q。说是『外传』，『内传』在那里呢？倘用『内传』， | Y sin embargo, al emprender este artículo, destinado a la rápida descomposición, apenas toco la pluma cuando siento una dificultad inmensa. Primero está el títu |
| 第二，立传的通例，开首大抵该是『某，字某，某地人也』，而我并不知道阿Q姓什么。有一回，他似乎是姓赵，但第二日便模糊了。那是赵太爷的儿子进了秀才的时候，锣声铛铛的报到村里来，阿Q正喝了两碗黄酒，便手舞足蹈的说，这于他也很光采，因为他和赵太爷原来是本家，细细的排起来他还比秀才长三辈呢。其时几个旁听人倒也肃然的有些起敬了。那 | Segundo, la convención para las biografías es que el principio suele ser «Tal, cuyo nombre de cortesía es Tal, es de Tal lugar», pero yo no sé el apellido de A- |
| 阿Q并没有抗辩他确凿姓赵，只用手摸着左颊，和地保退出去了；外面又被地保训斥了一番，谢了地保二百文酒钱。知道的人都说阿Q太荒唐，自己去招打；他大约未必姓赵，即使真姓赵，有赵太爷在这里，也不该如此胡说的。此后便再没有人提起他的氏族来，所以我终于不知道阿Q究竟什么姓。 | A-Q no defendió que su apellido fuera realmente Zhao, solo se tocó la mejilla izquierda con la mano y salió con el alguacil; afuera, el alguacil lo regañó de nu |

## bench-zhsrc-tg-zh-es: Simplified Chinese → Spanish (zh>es)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 150 LLM calls, 43.4 min; slowest: audit_translation 13.4 min, repair_translation 12.1 min, translate 10.2 min, extract_glossary 3.1 min
- **Glossary**: 114 approved entries, pair zh>es; e.g. 假洋鬼子 → el falso extranjero; 吴妈 → la sirvienta Wu; 地保 → el alguacil; 妲己 → Daji; 小D → el joven D; 小尼姑 → la joven monja; 少奶奶 → la joven señora; 王癞胡 → Wang Laihu
- **Style sheet characters**: 假洋鬼子 (él, tú); 吴妈 (ella, tú); 地保 (él, tú); 小D (él, tú); 小尼姑 (ella, tú); 少奶奶 (ella, tú); 王胡 (él, tú); 秀才 (él, tú)
- **Audit findings** (rules): {'glossary': 78, 'addition': 4, 'omission': 4, 'mistranslation': 3, 'duplication': 2}
- **Audit findings** (semantic model): {'mistranslation': 21, 'glossary': 3, 'untranslated': 2, 'omission': 2}
  - untranslated, e.g.: The translation 'el traidor' is an unsupported simplification of the specific glossary ter | The translation fails to render the source text, providing instead a formulaic expression 
  - numbers, e.g.: numeric content differs from source: In the SOURCE, '四五个响头' (four or five kowtows/head kno | numeric content differs from source: The source quantity '十几场' (ten-plus times/sessions) f | numeric content differs from source: The translation contains no quantity-bearing facts co
- **Book consistency**: 4 findings; [low] The style sheet renders "飘飘然的似乎要飞去了" as "flotaba como si fue ×2; [medium] An exclamation here has no opening ¡, but the book writes th ×1; [medium] A straight quotation mark " is used here, but the book uses  ×1
- **Review queue**: 16 segments
- **Target punctuation** (segments using each form, of 163): {'« » quotes': 88, '“ ” quotes': 0, '" straight quotes': 1, '¿ opening': 31, '? without ¿ in the segment': 0, '— raya dialogue': 14}

| source | translation |
|---|---|
| 我要给阿Q做正传，已经不止一两年了。但一面要做，一面又往回想，这足见我不是一个『立言』的人，因为从来不朽之笔，须传不朽之人，于是人以文传，文以人传——究竟谁靠谁传，渐渐的不甚了然起来，而终于归结到传阿Q，仿佛思想里有鬼似的。 | Ya hace más de un año que intento escribir la biografía legítima de A-Q. Pero mientras escribo, me vuelvo a mirar hacia atrás, lo cual demuestra que no soy un h |
| 然而要做这一篇速朽的文章，才下笔，便感到万分的困难了。第一是文章的名目。孔子曰：『名不正则言不顺。』这原是应该极注意的。传的名目很繁多：列传、自传、内传、外传、别传、家传、小传，……而可惜都不合。『列传』么，这一篇并非和许多阔人排在『正史』里；『自传』么，我又并非就是阿Q。说是『外传』，『内传』在那里呢？倘用『内传』， | Y sin embargo, al empezar a escribir este artículo, destinado a la rápida descomposición, sentí una dificultad inmensa. Primero, el título del artículo. Confuci |
| 第二，立传的通例，开首大抵该是『某，字某，某地人也』，而我并不知道阿Q姓什么。有一回，他似乎是姓赵，但第二日便模糊了。那是赵太爷的儿子进了秀才的时候，锣声铛铛的报到村里来，阿Q正喝了两碗黄酒，便手舞足蹈的说，这于他也很光采，因为他和赵太爷原来是本家，细细的排起来他还比秀才长三辈呢。其时几个旁听人倒也肃然的有些起敬了。那 | Segundo, la convención para las biografías es que el principio suele ser «Tal, cuyo nombre de cortesía es Tal, es de Tal lugar», pero yo no sé el apellido de A- |
| 阿Q并没有抗辩他确凿姓赵，只用手摸着左颊，和地保退出去了；外面又被地保训斥了一番，谢了地保二百文酒钱。知道的人都说阿Q太荒唐，自己去招打；他大约未必姓赵，即使真姓赵，有赵太爷在这里，也不该如此胡说的。此后便再没有人提起他的氏族来，所以我终于不知道阿Q究竟什么姓。 | A-Q no defendió que su apellido fuera realmente Zhao, solo se tocó la mejilla izquierda con la mano y salió con el alguacil; afuera, el alguacil lo reprendió de |

## bench-zhsrc-qwen-zh-ja: Simplified Chinese → Japanese (zh>ja)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: untranslated text, left-over source words, character report, prose rewrite
- **Cost**: 37 LLM calls, 13.5 min; slowest: audit_translation 3.4 min, translate 3.2 min, extract_glossary 3.0 min, resolve_glossary 1.5 min
- **Glossary**: 151 approved entries, pair zh>ja; e.g. 吴妈 → 呉媽; 妲己 → 妲己; 小D → 小D; 小Don → 小Don; 小尼姑 → 小尼姑; 少奶奶 → 少奶奶; 王癞胡 → 王癩胡; 王胡 → 王胡
- **Style sheet characters**: 假洋鬼子 (彼, —); 吴妈 (彼女, —); 地保 (彼, —); 小D (彼, —); 小尼姑 (彼女, —); 少奶奶 (彼女, 様); 王胡 (彼, —); 秀才 (彼, —)
- **Audit findings** (rules): {'glossary': 7, 'ai_style': 1}
- **Audit findings** (semantic model): {'mistranslation': 11}
- **Book consistency**: no findings
- **Review queue**: 2 segments
- **Target punctuation** (segments using each form, of 163): {'「」 quotes': 1, '" or “ ” quotes': 0, '…… ellipsis': 34, '… or ... ellipsis': 0}

| source | translation |
|---|---|
| 我要给阿Q做正传，已经不止一两年了。但一面要做，一面又往回想，这足见我不是一个『立言』的人，因为从来不朽之笔，须传不朽之人，于是人以文传，文以人传——究竟谁靠谁传，渐渐的不甚了然起来，而终于归结到传阿Q，仿佛思想里有鬼似的。 | 阿Qに正伝を作ろうと企てて、もう一二年は経つ。しかし書きながら、また振り返って思い返す。これは私が『立言』の人ではないことを十分に示している。なぜなら、古来、不朽の筆は不朽の人を伝え、人は文によって伝えられ、文は人によって伝えられるものだからだ。――結局、誰が誰を頼って伝わるのか、次第にわからなくなり、ついに阿Qの伝記 |
| 然而要做这一篇速朽的文章，才下笔，便感到万分的困难了。第一是文章的名目。孔子曰：『名不正则言不顺。』这原是应该极注意的。传的名目很繁多：列传、自传、内传、外传、别传、家传、小传，……而可惜都不合。『列传』么，这一篇并非和许多阔人排在『正史』里；『自传』么，我又并非就是阿Q。说是『外传』，『内传』在那里呢？倘用『内传』， | しかし、この速朽の文章を書こうとして、筆を執った途端、万感の困難を感じた。第一に、文章の題名である。孔子曰く、『名不正則言不顺。』これは本来、極めて注意すべきところだ。伝の題名は非常に多い：列伝、自伝、内伝、外伝、別伝、家伝、小伝、……だが、残念ながらどれも当てはまらない。『列伝』か、この一篇は多くの豪族と『正史』に並 |
| 第二，立传的通例，开首大抵该是『某，字某，某地人也』，而我并不知道阿Q姓什么。有一回，他似乎是姓赵，但第二日便模糊了。那是赵太爷的儿子进了秀才的时候，锣声铛铛的报到村里来，阿Q正喝了两碗黄酒，便手舞足蹈的说，这于他也很光采，因为他和赵太爷原来是本家，细细的排起来他还比秀才长三辈呢。其时几个旁听人倒也肃然的有些起敬了。那 | 第二に、伝記の通例では、冒頭は大概『某、字某、某地人也』であるべきだが、私は阿Qの姓を知らない。ある時、彼は趙姓のようだったが、翌日には曖昧になった。それは趙太爺の息子が秀才になった時で、太鼓の音がチンチンと村に届き、阿Qはちょうど二杯の黄酒を飲んだ後、手舞い足蹈びながら、これは自分にとっても光栄だと言った。なぜなら、 |
| 阿Q并没有抗辩他确凿姓赵，只用手摸着左颊，和地保退出去了；外面又被地保训斥了一番，谢了地保二百文酒钱。知道的人都说阿Q太荒唐，自己去招打；他大约未必姓赵，即使真姓赵，有赵太爷在这里，也不该如此胡说的。此后便再没有人提起他的氏族来，所以我终于不知道阿Q究竟什么姓。 | 阿Qは自分が確かに趙姓であることを反論せず、ただ手で左頬を撫で、地保と共に退いた。外ではまた地保に訓戒され、地保に二百文の酒銭を払った。知っている者は皆、阿Qはあまりに荒唐無稽で、自ら打たれを招いたと言う。彼は大概趙姓ではないだろうし、たとえ本当に趙姓でも、趙太爺がいる限り、そのような妄言はすべきではなかった。その後、 |

## bench-zhsrc-tg-zh-ja: Simplified Chinese → Japanese (zh>ja)

- **Status**: complete; tiers tuned → profiled
- **Skipped checks**: untranslated text, left-over source words, character report, prose rewrite
- **Cost**: 119 LLM calls, 45.4 min; slowest: translate 18.8 min, audit_translation 12.9 min, repair_translation 7.6 min, extract_glossary 3.0 min
- **Glossary**: 151 approved entries, pair zh>ja; e.g. 吴妈 → 呉媽; 妲己 → 妲己; 小D → 小D; 小Don → 小Don; 小尼姑 → 小尼姑; 少奶奶 → 少奶奶; 王癞胡 → 王癩胡; 王胡 → 王胡
- **Style sheet characters**: 假洋鬼子 (彼, —); 吴妈 (彼女, —); 地保 (彼, —); 小D (彼, —); 小尼姑 (彼女, —); 少奶奶 (彼女, 様); 王胡 (彼, —); 秀才 (彼, —)
- **Audit findings** (rules): {'glossary': 109, 'omission': 6, 'addition': 5, 'duplication': 3, 'mistranslation': 1}
- **Audit findings** (semantic model): {'mistranslation': 8, 'omission': 4, 'untranslated': 3, 'glossary': 2}
  - untranslated, e.g.: The translation fails to render the source meaning, replacing it with unrelated content. | The translation fails to render the source text, replacing it with unrelated prose. | The translation fails to render the source text, replacing it with unrelated content.
  - numbers, e.g.: numeric content differs from source: In the source, the length of the onion leaf is '半寸长' 
- **Book consistency**: 5 findings; [low] The style sheet renders "心满意足的得胜的走了" as "満足して勝利を収め、去った", but ×2; [low] The style sheet renders "飘飘然的似乎要飞去了" as "飄々として飛んでいくかのようだった", ×2; [low] The style sheet renders "你算是什么东西" as "君は一体何様のつもりだ", but this ×1
- **Review queue**: 0 segments
- **Target punctuation** (segments using each form, of 163): {'「」 quotes': 70, '" or “ ” quotes': 0, '…… ellipsis': 28, '… or ... ellipsis': 0}

| source | translation |
|---|---|
| 我要给阿Q做正传，已经不止一两年了。但一面要做，一面又往回想，这足见我不是一个『立言』的人，因为从来不朽之笔，须传不朽之人，于是人以文传，文以人传——究竟谁靠谁传，渐渐的不甚了然起来，而终于归结到传阿Q，仿佛思想里有鬼似的。 | 私は阿Qの伝記を書こうと、もう一年以上も前から考えてきた。しかし、書き始めようとすると、また考え直してしまう。これは、私が「立言」をする人間ではないことを示している。なぜなら、不朽の筆は、不朽の人を伝えるべきであり、人は文章によって伝えられ、文章は人によって伝えられるからだ。結局、誰が誰を伝えるのか、だんだんわからなく |
| 然而要做这一篇速朽的文章，才下笔，便感到万分的困难了。第一是文章的名目。孔子曰：『名不正则言不顺。』这原是应该极注意的。传的名目很繁多：列传、自传、内传、外传、别传、家传、小传，……而可惜都不合。『列传』么，这一篇并非和许多阔人排在『正史』里；『自传』么，我又并非就是阿Q。说是『外传』，『内传』在那里呢？倘用『内传』， | しかし、この速朽するような文章を書こうとすると、すぐに多くの困難に直面する。まず、文章の題名だ。孔子は「名不正則言不順」と言った。これは、非常に注意すべきことだ。伝記には、列伝、自伝、内伝、外伝、別伝、家伝、小伝など、さまざまな種類がある。しかし、残念ながら、どれも阿Qには当てはまらない。『列伝』は、多くの著名人と並ん |
| 第二，立传的通例，开首大抵该是『某，字某，某地人也』，而我并不知道阿Q姓什么。有一回，他似乎是姓赵，但第二日便模糊了。那是赵太爷的儿子进了秀才的时候，锣声铛铛的报到村里来，阿Q正喝了两碗黄酒，便手舞足蹈的说，这于他也很光采，因为他和赵太爷原来是本家，细细的排起来他还比秀才长三辈呢。其时几个旁听人倒也肃然的有些起敬了。那 | 第二に、伝記を書く際の一般的な形式は、冒頭に「〇〇、字〇〇、〇〇の出身」と書くことだ。しかし、私は阿Qの姓が何であるかわからない。ある時、彼は趙姓のようだったが、次の日には曖昧になってしまった。それは、趙太爺の息子が秀才になった時、村に太鼓の音が響き渡り、阿Qは酒を二杯飲んで、手足を振り回しながら言った。「これは、私に |
| 阿Q并没有抗辩他确凿姓赵，只用手摸着左颊，和地保退出去了；外面又被地保训斥了一番，谢了地保二百文酒钱。知道的人都说阿Q太荒唐，自己去招打；他大约未必姓赵，即使真姓赵，有赵太爷在这里，也不该如此胡说的。此后便再没有人提起他的氏族来，所以我终于不知道阿Q究竟什么姓。 | 阿Qは、自分が本当に趙姓であることを否定しなかった。ただ、左の頬をさすりながら、地保と一緒にそこから出て行った。外に出ると、地保からさらに叱責を受け、二百文の酒代を渡された。人々は、阿Qがあまりにも無分別で、自分から殴られるようにしたのだと言った。彼は、もしかしたら趙姓ではないかもしれない。もし本当に趙姓だとしても、趙 |


# Language-profile benchmarks

## bench-zhsrc2-tg-zh-de: Simplified Chinese → German (zh>de)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 259 LLM calls, 72.6 min; slowest: audit_translation 36.5 min, translate 20.0 min, repair_translation 9.4 min, extract_glossary 2.5 min
- **Glossary**: 98 approved entries, pair zh>de; e.g. 假洋鬼子 → Falscher Ausländer; 吴妈 → Mutter Wu; 小D → Kleiner D; 小Don → Kleiner Don; 小尼姑 → die kleine Nonne; 王癞胡 → Wang Laih; 王胡 → Wang Hu; 老尼姑 → die alte Nonne
- **Style sheet characters**: 假洋鬼子 (er, Sie); 吴妈 (sie, Sie); 地保 (er, Sie); 小D (er, du); 小尼姑 (sie, du); 王胡 (er, du); 秀才 (er, Sie); 老尼姑 (sie, du)
- **Audit findings** (rules): {'glossary': 42, 'mistranslation': 6, 'duplication': 1}
- **Audit findings** (semantic model): {'mistranslation': 37, 'glossary': 8, 'untranslated': 6, 'omission': 6, 'structure': 1}
  - untranslated, e.g.: The translation omits the final punctuation of the last sentence, which in the source is a | The translation introduces 'Wang Hu' into the second sentence, but this name does not appe | The translation omits the factual proposition that A-Q had become more proud of himself. | The translation omits the specific detail that A-Q was 'exceptionally brave' (非常武勇) this t | The translation adds 'sein' (his) to 'Hemd', but the source '布衫' (cloth shirt) refers to t
  - numbers, e.g.: numeric content differs from source: In the source, '不入三教九流' (not belonging to the three t | numeric content differs from source: The source describes 'four or five head-knocks' (四五个响 | numeric content differs from source: SOURCE: '十几场' (ten-plus times/over ten times) refers 
- **Book consistency**: 4 findings; [low] The style sheet renders "心满意足的得胜的走了" as "zufrieden und siegr ×2; [low] The style sheet renders "飘飘然的似乎要飞去了" as "schwebend, als würd ×2
- **Review queue**: 13 segments
- **Target punctuation** (segments using each form, of 163): {'„ “ quotes': 89, '» « quotes': 0, '” English closing quote': 0, '" straight quotes': 0, '– Gedankenstrich': 14}

| source | translation |
|---|---|
| 我要给阿Q做正传，已经不止一两年了。但一面要做，一面又往回想，这足见我不是一个『立言』的人，因为从来不朽之笔，须传不朽之人，于是人以文传，文以人传——究竟谁靠谁传，渐渐的不甚了然起来，而终于归结到传阿Q，仿佛思想里有鬼似的。 | Ich beabsichtige, eine Biographie des A-Q zu verfassen, seit nunmehr mehr als ein oder zwei Jahren. Doch während ich schreibe, blicke ich zugleich zurück; dies  |
| 然而要做这一篇速朽的文章，才下笔，便感到万分的困难了。第一是文章的名目。孔子曰：『名不正则言不顺。』这原是应该极注意的。传的名目很繁多：列传、自传、内传、外传、别传、家传、小传，……而可惜都不合。『列传』么，这一篇并非和许多阔人排在『正史』里；『自传』么，我又并非就是阿Q。说是『外传』，『内传』在那里呢？倘用『内传』， | Doch kaum hatte ich mit diesem schnell vergänglichen Artikel begonnen, da erfasste mich eine unermessliche Schwierigkeit. Erstens der Titel. Konfuzius sprach: „ |
| 第二，立传的通例，开首大抵该是『某，字某，某地人也』，而我并不知道阿Q姓什么。有一回，他似乎是姓赵，但第二日便模糊了。那是赵太爷的儿子进了秀才的时候，锣声铛铛的报到村里来，阿Q正喝了两碗黄酒，便手舞足蹈的说，这于他也很光采，因为他和赵太爷原来是本家，细细的排起来他还比秀才长三辈呢。其时几个旁听人倒也肃然的有些起敬了。那 | Zweitens: Die übliche Einleitung einer Biographie lautet meist: „So und so, mit dem Beinamen So und so, aus dem Ort So und so.“ Doch ich weiß nicht, welchen Nac |
| 阿Q并没有抗辩他确凿姓赵，只用手摸着左颊，和地保退出去了；外面又被地保训斥了一番，谢了地保二百文酒钱。知道的人都说阿Q太荒唐，自己去招打；他大约未必姓赵，即使真姓赵，有赵太爷在这里，也不该如此胡说的。此后便再没有人提起他的氏族来，所以我终于不知道阿Q究竟什么姓。 | Der A-Q widersprach nicht, dass er tatsächlich Zhao hieß; er berührte nur seine linke Wange und ging mit dem Dibao hinaus. Draußen wurde er vom Dibao gescholten |

## bench-zhsrc2-tg-zh-es: Simplified Chinese → Spanish (zh>es)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 142 LLM calls, 37.7 min; slowest: audit_translation 14.6 min, translate 11.2 min, repair_translation 5.2 min, extract_glossary 3.1 min
- **Glossary**: 114 approved entries, pair zh>es; e.g. 假洋鬼子 → el falso extranjero; 吴妈 → la sirvienta Wu; 地保 → el alguacil; 妲己 → Daji; 小D → el joven D; 小尼姑 → la joven monja; 少奶奶 → la joven señora; 王癞胡 → Wang Laihu
- **Style sheet characters**: 假洋鬼子 (él, tú); 吴妈 (ella, tú); 地保 (él, tú); 小D (él, tú); 小尼姑 (ella, tú); 少奶奶 (ella, tú); 王胡 (él, tú); 秀才 (él, tú)
- **Audit findings** (rules): {'glossary': 82, 'mistranslation': 3, 'duplication': 1}
- **Audit findings** (semantic model): {'mistranslation': 10, 'glossary': 4, 'untranslated': 2}
  - untranslated, e.g.: The translation fails to use the mandatory glossary rendering for '里通外国'. | The translation 'el traidor' is an unsupported simplification of the specific glossary ter
  - numbers, e.g.: numeric content differs from source: The source quantity '四百' (400) refers to the value of | numeric content differs from source: The source quantity '十几场' (ten-plus times/sessions) f | numeric content differs from source: The source text contains no quantity-bearing facts; h
- **Book consistency**: 2 findings; [medium] A straight quotation mark " is used here, but the book uses  ×1; [low] The style sheet renders "飘飘然的似乎要飞去了" as "flotaba como si fue ×1
- **Review queue**: 10 segments
- **Target punctuation** (segments using each form, of 163): {'« » quotes': 87, '“ ” quotes': 0, '" straight quotes': 0, '¿ opening': 32, '? without ¿ in the segment': 0, '— raya dialogue': 5}

| source | translation |
|---|---|
| 我要给阿Q做正传，已经不止一两年了。但一面要做，一面又往回想，这足见我不是一个『立言』的人，因为从来不朽之笔，须传不朽之人，于是人以文传，文以人传——究竟谁靠谁传，渐渐的不甚了然起来，而终于归结到传阿Q，仿佛思想里有鬼似的。 | Hace ya más de un año que quiero escribir la biografía de A-Q. Sin embargo, mientras pienso en ello, me doy cuenta de que no soy un hombre que se dedique a «est |
| 然而要做这一篇速朽的文章，才下笔，便感到万分的困难了。第一是文章的名目。孔子曰：『名不正则言不顺。』这原是应该极注意的。传的名目很繁多：列传、自传、内传、外传、别传、家传、小传，……而可惜都不合。『列传』么，这一篇并非和许多阔人排在『正史』里；『自传』么，我又并非就是阿Q。说是『外传』，『内传』在那里呢？倘用『内传』， | Sin embargo, al intentar escribir este breve artículo, que seguramente se olvidará pronto, me encuentro con grandes dificultades. En primer lugar, está el títul |
| 第二，立传的通例，开首大抵该是『某，字某，某地人也』，而我并不知道阿Q姓什么。有一回，他似乎是姓赵，但第二日便模糊了。那是赵太爷的儿子进了秀才的时候，锣声铛铛的报到村里来，阿Q正喝了两碗黄酒，便手舞足蹈的说，这于他也很光采，因为他和赵太爷原来是本家，细细的排起来他还比秀才长三辈呢。其时几个旁听人倒也肃然的有些起敬了。那 | En segundo lugar, en general, al principio de una biografía, se suele decir: «Tal, de nombre Tal, es originario de Tal». Pero no sé cuál es el apellido de A-Q.  |
| 阿Q并没有抗辩他确凿姓赵，只用手摸着左颊，和地保退出去了；外面又被地保训斥了一番，谢了地保二百文酒钱。知道的人都说阿Q太荒唐，自己去招打；他大约未必姓赵，即使真姓赵，有赵太爷在这里，也不该如此胡说的。此后便再没有人提起他的氏族来，所以我终于不知道阿Q究竟什么姓。 | A-Q no se defendió, sino que se tocó la mejilla izquierda y, junto con el alguacil, salió. Afuera, el alguacil lo regañó y A-Q le dio doscientos wen para que be |

## bench-zhsrc2-tg-zh-ja: Simplified Chinese → Japanese (zh>ja)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: untranslated text, left-over source words, character report, prose rewrite
- **Cost**: 95 LLM calls, 38.0 min; slowest: translate 20.1 min, audit_translation 8.2 min, repair_translation 3.6 min, extract_glossary 3.0 min
- **Glossary**: 151 approved entries, pair zh>ja; e.g. 吴妈 → 呉媽; 妲己 → 妲己; 小D → 小D; 小Don → 小Don; 小尼姑 → 小尼姑; 少奶奶 → 少奶奶; 王癞胡 → 王癩胡; 王胡 → 王胡
- **Style sheet characters**: 假洋鬼子 (彼, —); 吴妈 (彼女, —); 地保 (彼, —); 小D (彼, —); 小尼姑 (彼女, —); 少奶奶 (彼女, 様); 王胡 (彼, —); 秀才 (彼, —)
- **Audit findings** (rules): {'glossary': 74, 'duplication': 3, 'addition': 3, 'omission': 2, 'mistranslation': 1}
- **Audit findings** (semantic model): {'mistranslation': 6, 'glossary': 2, 'untranslated': 2, 'omission': 1}
  - untranslated, e.g.: The translation fails to render any of the source's propositions. | The translation fails to render any part of the source text.
  - numbers, e.g.: numeric content differs from source: In the source, the length of the onion leaf is '半寸长' 
- **Book consistency**: no findings
- **Review queue**: 1 segments
- **Target punctuation** (segments using each form, of 163): {'「」 quotes': 77, '" or “ ” quotes': 0, '…… ellipsis': 28, '… or ... ellipsis': 0}

| source | translation |
|---|---|
| 我要给阿Q做正传，已经不止一两年了。但一面要做，一面又往回想，这足见我不是一个『立言』的人，因为从来不朽之笔，须传不朽之人，于是人以文传，文以人传——究竟谁靠谁传，渐渐的不甚了然起来，而终于归结到传阿Q，仿佛思想里有鬼似的。 | 私は阿Qの伝記を書こうと、もう一年以上も前から考えてきた。しかし、書き始めようとすると、また考え直してしまう。これは、私が「立言」をする人間ではないことを示している。なぜなら、不朽の筆は、不朽の人を伝えるべきであり、人は文章によって伝えられ、文章は人によって伝えられるからだ。結局、誰が誰を伝えるのか、だんだんわからなく |
| 然而要做这一篇速朽的文章，才下笔，便感到万分的困难了。第一是文章的名目。孔子曰：『名不正则言不顺。』这原是应该极注意的。传的名目很繁多：列传、自传、内传、外传、别传、家传、小传，……而可惜都不合。『列传』么，这一篇并非和许多阔人排在『正史』里；『自传』么，我又并非就是阿Q。说是『外传』，『内传』在那里呢？倘用『内传』， | しかし、この速朽するような文章を書こうとすると、すぐに多くの困難に直面する。まず、文章の題名だ。孔子は「名不正則言不順」と言った。これは、非常に注意すべきことだ。伝記には、列伝、自伝、内伝、外伝、別伝、家伝、小伝など、さまざまな種類がある。しかし、残念ながら、どれも阿Qには当てはまらない。『列伝』は、多くの著名人と並ん |
| 第二，立传的通例，开首大抵该是『某，字某，某地人也』，而我并不知道阿Q姓什么。有一回，他似乎是姓赵，但第二日便模糊了。那是赵太爷的儿子进了秀才的时候，锣声铛铛的报到村里来，阿Q正喝了两碗黄酒，便手舞足蹈的说，这于他也很光采，因为他和赵太爷原来是本家，细细的排起来他还比秀才长三辈呢。其时几个旁听人倒也肃然的有些起敬了。那 | 第二に、伝記を書く際の一般的な形式は、冒頭に「〇〇、字〇〇、〇〇の出身」と書くことだ。しかし、私は阿Qの姓が何であるかわからない。ある時、彼は趙姓のようだったが、次の日には曖昧になってしまった。それは、趙太爺の息子が秀才になった時、村に太鼓の音が響き渡り、阿Qは酒を二杯飲んで、手足を振り回しながら言った。「これは、私に |
| 阿Q并没有抗辩他确凿姓赵，只用手摸着左颊，和地保退出去了；外面又被地保训斥了一番，谢了地保二百文酒钱。知道的人都说阿Q太荒唐，自己去招打；他大约未必姓赵，即使真姓赵，有赵太爷在这里，也不该如此胡说的。此后便再没有人提起他的氏族来，所以我终于不知道阿Q究竟什么姓。 | 阿Qは、自分が本当に趙姓であることを否定しなかった。ただ、左の頬をさすりながら、地保と一緒にそこから出て行った。外に出ると、地保からさらに叱責を受け、二百文の酒代を渡された。人々は、阿Qがあまりにも無分別で、自分から殴られるようにしたのだと言った。彼は、もしかしたら趙姓ではないかもしれない。もし本当に趙姓だとしても、趙 |


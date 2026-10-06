# Language-profile benchmarks

## bench-tg2-de-en: German → English (de>en)

- **Status**: paused; tiers profiled → tuned
- **Skipped checks**: untranslated text, punctuation conventions, character report
- **Cost**: 55 LLM calls, 62.1 min; slowest: repair_translation 25.7 min, audit_translation 23.5 min, translate 9.7 min, extract_glossary 1.3 min
- **Glossary**: 18 approved entries, pair de>en; e.g. Anna → Anna; Gregor → Gregor; Gregor Samsa → Gregor Samsa; Grete → Grete; Mutter → Mother; Samsa → Samsa; Vater → Father; Musterkollektion → Sample collection
- **Style sheet characters**: Gregor Samsa (he, —); Mutter (she, —); Prokurist (he, —); Vater (he, —)
- **Audit findings** (rules): {'glossary': 3, 'untranslated': 2}
- **Audit findings** (semantic model): {'mistranslation': 5, 'omission': 1}
  - untranslated, e.g.: possible untranslated German passage: sagte Gregor, wohl wissend, dass er der Einzige war, | possible untranslated German passage: Gregor sah ein, dass er den Prokuristen in dieser St
- **Book consistency**: 2 findings; [low] The style sheet renders "um Gottes willen" as "for God's sak ×2
- **Review queue**: 2 segments
- **Target punctuation** (segments using each form, of 31): {'“ ” quotes': 20, '" straight quotes': 0}

| source | translation |
|---|---|
| Als Gregor Samsa eines Morgens aus unruhigen Träumen erwachte, fand er sich in seinem Bett zu einem ungeheueren Ungeziefer verwandelt. Er lag auf seinem panzera | When Gregor Samsa awoke one morning from troubled dreams, he found himself transformed in his bed into a monstrous vermin. He lay on his armor-like hard back an |
| »Was ist mit mir geschehen?« dachte er. Es war kein Traum. Sein Zimmer, ein richtiges, nur etwas zu kleines Menschenzimmer, lag ruhig zwischen den vier wohlbeka | “What has happened to me?” he thought. It was no dream. His room, a proper one, only somewhat too small for a human room, lay quiet between the four well-known  |
| Gregors Blick richtete sich dann zum Fenster, und das trübe Wetter – man hörte Regentropfen auf das Fensterblech aufschlagen – machte ihn ganz melancholisch. »W | Gregor’s gaze then turned to the window, and the gloomy weather — one could hear raindrops striking the window shutter — made him quite melancholy. “How about i |
| »Ach Gott,« dachte er, »was für einen anstrengenden Beruf habe ich gewählt! Tag aus, Tag ein auf der Reise. Die geschäftlichen Aufregungen sind viel größer, als | “Oh God,” he thought, “what a strenuous profession I have chosen! Day in, day out on the road. The stresses of business are much greater than in the actual busi |

## bench-tg2-en-ko: English → Korean (en>ko)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 142 LLM calls, 31.2 min; slowest: translate 13.6 min, audit_translation 7.6 min, repair_translation 5.6 min, extract_glossary 1.4 min
- **Glossary**: 43 approved entries, pair en>ko; e.g. Ada → 아다; Alice → 앨리스; Bill → 빌; caterpillar → 나방; Crab → 게; Dinah → 디나; Dodo → 도도새; Duchess → 공작부인
- **Style sheet characters**: Alice (그녀, —); Bill (그, —); caterpillar (그, —); Crab (그, —); Dodo (그, —); Duck (그, —); Eaglet (그, —); Lory (그, —)
- **Audit findings** (rules): {'glossary': 15, 'mistranslation': 5, 'addition': 2, 'untranslated': 1}
- **Audit findings** (semantic model): {'mistranslation': 9, 'omission': 3, 'untranslated': 3}
  - untranslated, e.g.: possible untranslated English phrase: est ma chatte | The translation introduces the name '앨리스' (Alice), which does not appear in the source seg | The translation fails to render the specific characters 'Lizard' and 'Bill' from the appro | The translation fails to render any of the factual propositions of the source segment, rep
  - numbers, e.g.: numeric content differs from source: In SOURCE, the Mouse says 'I’ll tell you my history'  | numeric content differs from source: The source quantity 'half an hour or so' (approximate | numeric content differs from source: The polarity of the quantity-bearing sentiment in the
- **Book consistency**: 10 findings; [low] The style sheet renders "yer honour" as "명령하소서", but this tr ×3; [low] The style sheet renders "Oh dear!" as "아이고!", but this trans ×2; [low] The style sheet renders "I beg your pardon!" as "실례를 용서해 주십시 ×2; [low] The style sheet renders "old fellow" as "오래된 친구", but this t ×2; [low] Repeated quoted line "I beg your pardon" is rendered "죄송합니다. ×1
- **Review queue**: 9 segments
- **Target punctuation** (segments using each form, of 145): {'“ ” quotes': 112, '" straight quotes': 0, '「」 quotes': 0, '…… ellipsis': 0}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | 앨리스는 강둑에 앉아 있는 여동생 옆에 앉아 있는 것이 점점 지루해지고, 할 일이 없어지자, 여동생이 읽고 있는 책을 흘끗 보았지만, 그림이나 대화가 없어서 “그림이나 대화가 없는 책은 무슨 소용일까?”라고 생각했다. |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | 그래서 앨리스는 마음속으로 생각했다(더운 날씨 때문에 앨리스는 매우 졸리고 어리석게 느껴졌지만), 데이지 꽃으로 목걸이를 만드는 즐거움이 데이지를 꺾으러 일어나서 하는 수고로움만큼 가치가 있을지. 그러자 갑자기 분홍색 눈을 가진 흰 토끼가 그녀 옆을 빠르게 지나갔다. |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | 그것에 특별히 눈에 띄는 점은 없었고, 앨리스도 토끼가 “아이고! 아이고! 늦겠어!”라고 혼잣말하는 것이 그렇게 이상하다고 생각하지 않았다(나중에 다시 생각해보니, 그 점에 대해 의아해했어야 한다고 생각했지만, 그때는 모두 자연스럽게 느껴졌다). 하지만 토끼가 실제로 조끼 주머니에서 시 |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | 잠시 후 앨리스도 토끼를 따라 굴 속으로 뛰어들었고, 어떻게 다시 밖으로 나올 수 있을지 전혀 생각하지 않았다. |

## bench-tg2-en-de: English → German (en>de)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: untranslated text, character report, prose rewrite
- **Cost**: 81 LLM calls, 23.2 min; slowest: translate 12.5 min, audit_translation 6.0 min, extract_glossary 1.5 min, repair_translation 1.1 min
- **Glossary**: 39 approved entries, pair en>de; e.g. Ada → Ada; Alice → Alice; Bill → Bill; Dinah → Dinah; Edgar Atheling → Edgar Atheling; Edwin → Edwin; Mabel → Mabel; Mary Ann → Mary Ann
- **Style sheet characters**: Alice (sie, du); Bill (er, du); Crab (sie, du); Dinah (sie, du); Dodo (es, Sie); Duck (sie, Sie); Eaglet (es, Sie); Lory (es, Sie)
- **Audit findings** (rules): {'mistranslation': 2, 'glossary': 2}
- **Audit findings** (semantic model): {'mistranslation': 8}
  - numbers, e.g.: numeric content differs from source: The quantity 'more than nine feet high' (value: more  | numeric content differs from source: The depth of the pool is changed from 'four inches' (
- **Book consistency**: 1 findings; [medium] An English closing quotation mark ” is used here, but the bo ×1
- **Review queue**: 1 segments
- **Target punctuation** (segments using each form, of 145): {'„ “ quotes': 113, '» « quotes': 0, '” English closing quote': 0, '" straight quotes': 0, '– Gedankenstrich': 17}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | Alice begann, es leid zu werden, neben ihrer Schwester auf der Böschung zu sitzen und nichts zu tun: Ein- oder zweimal hatte sie in das Buch hineingelinst, das  |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | Also überlegte sie in ihrem eigenen Kopf (so gut sie konnte, denn das heiße Wetter ließ sie sehr schläfrig und dumm fühlen), ob die Freude, eine Gänseblümchenke |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | Es war nichts besonders Bemerkenswertes daran; und Alice fand es auch nicht besonders ungewöhnlich, den Hasen zu hören, wie er zu sich selbst sagte: „Oh je! Oh  |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | Im nächsten Moment fiel Alice ihm nach, ohne auch nur einen Augenblick darüber nachzudenken, wie sie wieder herauskommen sollte. |

## bench-tg2-es-en: Spanish → English (es>en)

- **Status**: paused; tiers profiled → tuned
- **Skipped checks**: untranslated text, punctuation conventions, character report
- **Cost**: 142 LLM calls, 44.5 min; slowest: audit_translation 18.3 min, translate 14.9 min, extract_glossary 4.1 min, repair_translation 2.9 min
- **Glossary**: 168 approved entries, pair es>en; e.g. Albert → Albert; Alburquerque → Albuquerque; Alejandro Miquis → Alejandro Miquis; Arnaiz → Arnaiz; Ayún → Ayún; Baldomerito → Baldomerito; Baldomero → Baldomero; Barbarita → Barbarita
- **Style sheet characters**: Arnaiz (he, —); Baldomero (he, —); Barbarita (she, —); doña Asunción Trujillo (she, —); Gumersindo Arnaiz (he, —); Gustavito (he, —); Isabel Cordero (she, —); Jacinto María Villalonga (he, —)
- **Audit findings** (rules): {'glossary': 19, 'mistranslation': 8}
- **Audit findings** (semantic model): {'mistranslation': 13}
  - numbers, e.g.: numeric content differs from source: In the source, 'daba quince y raya' is an idiomatic e | numeric content differs from source: SOURCE: "por los años del 10 al 15" (range: 1810-1815 | numeric content differs from source: The source range "del 45 al 55" refers to the years 1
- **Book consistency**: 2 findings; [low] The style sheet renders "el gordo Arnaiz" as "the fat Arnaiz ×2
- **Review queue**: 3 segments
- **Target punctuation** (segments using each form, of 88): {'“ ” quotes': 13, '" straight quotes': 15}

| source | translation |
|---|---|
| Las noticias más remotas que tengo de la persona que lleva este nombre me las ha dado Jacinto María Villalonga, y alcanzan al tiempo en que este amigo mío y el  | The earliest news I have of the person who bears this name comes from Jacinto María Villalonga, and it dates back to the time when this friend of mine, and the  |
| ¡Ay!, el susto que se llevaron D. Baldomero Santa Cruz y Barbarita no es para contado. ¡Qué noche de angustia la del 10 al 11! Ambos creían no volver a ver a su | Oh, the fright that Don Baldomero Santa Cruz and Barbarita experienced is beyond description. What a night of anguish from the 10th to the 11th! Both believed t |
| Cuando el niño estudiaba los últimos años de su carrera, verificose en él uno de esos cambiazos críticos que tan comunes son en la edad juvenil. De travieso y a | When the boy was studying the last years of his degree, he underwent one of those critical changes that are so common in youth. He went from being mischievous a |
| Todos los dineros que su papá le daba, dejábalos Juanito en casa de Bailly-Baillière, a cuenta de los libros que iba tomando. Refiere Villalonga que un día fue  | All the money that his father gave him, Juanito left it at Bailly-Baillière, as payment for the books he was buying. Villalonga tells that one day Barbarita wen |


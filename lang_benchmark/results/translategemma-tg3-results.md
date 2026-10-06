# Language-profile benchmarks

## bench-tg3-de-en: German → English (de>en)

- **Status**: complete; tiers profiled → tuned
- **Skipped checks**: untranslated text, punctuation conventions, character report
- **Cost**: 50 LLM calls, 20.8 min; slowest: audit_translation 9.1 min, translate 8.1 min, repair_translation 1.4 min, extract_glossary 1.1 min
- **Glossary**: 18 approved entries, pair de>en; e.g. Anna → Anna; Gregor → Gregor; Gregor Samsa → Gregor Samsa; Grete → Grete; Mutter → Mother; Samsa → Samsa; Vater → Father; Musterkollektion → Sample collection
- **Style sheet characters**: Gregor Samsa (he, —); Mutter (she, —); Prokurist (he, —); Vater (he, —)
- **Audit findings** (rules): {}
- **Audit findings** (semantic model): {'mistranslation': 10, 'omission': 1}
- **Book consistency**: 2 findings; [low] The style sheet renders "um Gottes willen" as "for God's sak ×2
- **Review queue**: 0 segments
- **Target punctuation** (segments using each form, of 31): {'“ ” quotes': 20, '" straight quotes': 0}

| source | translation |
|---|---|
| Als Gregor Samsa eines Morgens aus unruhigen Träumen erwachte, fand er sich in seinem Bett zu einem ungeheueren Ungeziefer verwandelt. Er lag auf seinem panzera | When Gregor Samsa awoke one morning from troubled dreams, he found himself transformed in his bed into a monstrous vermin. He lay on his armor-like hard back an |
| »Was ist mit mir geschehen?« dachte er. Es war kein Traum. Sein Zimmer, ein richtiges, nur etwas zu kleines Menschenzimmer, lag ruhig zwischen den vier wohlbeka | “What has happened to me?” he thought. It was no dream. His room, a proper one, only somewhat too small for a human room, lay quiet between the four well-known  |
| Gregors Blick richtete sich dann zum Fenster, und das trübe Wetter – man hörte Regentropfen auf das Fensterblech aufschlagen – machte ihn ganz melancholisch. »W | Gregor’s gaze then turned to the window, and the gloomy weather — one could hear raindrops striking the window shutter — made him quite melancholy. “How about i |
| »Ach Gott,« dachte er, »was für einen anstrengenden Beruf habe ich gewählt! Tag aus, Tag ein auf der Reise. Die geschäftlichen Aufregungen sind viel größer, als | “Oh God,” he thought, “what a strenuous profession I have chosen! Day in, day out on the road. The stresses of business are much greater than in the actual busi |

## bench-tg3-en-ko: English → Korean (en>ko)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 127 LLM calls, 39.5 min; slowest: translate 14.8 min, audit_translation 13.9 min, repair_translation 5.9 min, extract_glossary 1.4 min
- **Glossary**: 43 approved entries, pair en>ko; e.g. Ada → 아다; Alice → 앨리스; Bill → 빌; caterpillar → 나방; Crab → 게; Dinah → 디나; Dodo → 도도새; Duchess → 공작부인
- **Style sheet characters**: Alice (그녀, —); Bill (그, —); caterpillar (그, —); Crab (그, —); Dodo (그, —); Duck (그, —); Eaglet (그, —); Lory (그, —)
- **Audit findings** (rules): {'glossary': 7, 'untranslated': 4, 'mistranslation': 4, 'addition': 1, 'ai_style': 1}
- **Audit findings** (semantic model): {'mistranslation': 15, 'untranslated': 1, 'omission': 1}
  - untranslated, e.g.: possible untranslated English word: splash | possible untranslated English word: vulgar | text in a script neither language uses: 她去 | The translation introduces the name '앨리스' (Alice), which does not appear in the source seg | text in a script neither language uses: 着手
  - numbers, e.g.: numeric content differs from source: The source quantity 'half an hour or so' (approximate | numeric content differs from source: The polarity of the quantity-bearing sentiment in the | numeric content differs from source: SOURCE: 'about a thousand times as large as the Rabbi
- **Book consistency**: 5 findings; [low] The style sheet renders "yer honour" as "명령하소서", but this tr ×3; [low] Repeated quoted line "I beg your pardon" is rendered "죄송합니다. ×1; [low] The style sheet renders "old fellow" as "오래된 친구", but this t ×1
- **Review queue**: 4 segments
- **Target punctuation** (segments using each form, of 145): {'“ ” quotes': 113, '" straight quotes': 0, '「」 quotes': 0, '…… ellipsis': 0}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | 앨리스는 강둑에 앉아 있는 여동생 옆에 앉아 있는 것이 점점 지루해지고, 할 일이 없어지자, 여동생이 읽고 있는 책을 흘끗 보았지만, 그림이나 대화가 없어서 “그림이나 대화가 없는 책은 무슨 소용일까?”라고 생각했다. |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | 그래서 앨리스는 마음속으로 생각했다(더운 날씨 때문에 앨리스는 매우 졸리고 어리석게 느껴졌지만), 데이지 꽃으로 목걸이를 만드는 즐거움이 데이지를 꺾으러 일어나서 하는 수고로움만큼 가치가 있을지. 그러자 갑자기 분홍색 눈을 가진 흰 토끼가 그녀 옆을 빠르게 지나갔다. |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | 그것에 특별히 눈에 띄는 점은 없었고, 앨리스도 토끼가 “아이고! 아이고! 늦겠어!”라고 혼잣말하는 것이 그렇게 이상하다고 생각하지 않았다(나중에 다시 생각해보니, 그 점에 대해 의아해했어야 한다고 생각했지만, 그때는 모두 자연스럽게 느껴졌다). 하지만 토끼가 실제로 조끼 주머니에서 시 |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | 잠시 후 앨리스도 토끼를 따라 굴 속으로 뛰어들었고, 어떻게 다시 밖으로 나올 수 있을지 전혀 생각하지 않았다. |

## bench-tg3-en-es: English → Spanish (en>es)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: untranslated text, character report, prose rewrite
- **Cost**: 85 LLM calls, 34.3 min; slowest: audit_translation 12.4 min, translate 12.2 min, repair_translation 4.1 min, extract_glossary 1.8 min
- **Glossary**: 39 approved entries, pair en>es; e.g. Ada → Ada; Alice → Alicia; Bill → Bill; Canary → Canario; Crab → Cangrejo; Dinah → Dinah; Dodo → Dodo; Duchess → Duquesa
- **Style sheet characters**: Alice (ella, usted); Bill (él, tú); Caterpillar (él, usted); Crab (él, usted); Dinah (ella, tú); Dodo (él, usted); Duck (él, usted); Eaglet (él, usted)
- **Audit findings** (rules): {'glossary': 4}
- **Audit findings** (semantic model): {'mistranslation': 11, 'omission': 2, 'glossary': 1}
- **Book consistency**: 7 findings; [medium] An exclamation here has no opening ¡, but the book writes th ×4; [medium] A question here has no opening ¿, but the book writes them ( ×1; [low] The style sheet renders "Do cats eat bats?" as "¿Comen los g ×1; [medium] Repeated quoted line "I beg your pardon" is rendered "Le pid ×1
- **Review queue**: 2 segments
- **Target punctuation** (segments using each form, of 145): {'« » quotes': 25, '“ ” quotes': 89, '" straight quotes': 1, '¿ opening': 42, '? without ¿ in the segment': 0, '— raya dialogue': 7}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | Alicia comenzaba a cansarse mucho de estar sentada junto a su hermana en la orilla del río, y de no tener nada que hacer: una o dos veces había espiado el libro |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | Así que lo estaba considerando en su propia mente (tanto como podía, porque el calor del día la hacía sentir muy somnolienta y aturdida), si el placer de hacer  |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | No había nada de tan extraordinario en eso; ni tampoco Alicia pensó que fuera algo tan inusual oír al Conejo decir para sí mismo: “¡Ay, Dios mío! ¡Ay, Dios mío! |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | En un instante, Alicia lo siguió, sin considerar ni por un momento cómo iba a salir de allí. |

## bench-tg3-en-fr: English → French (en>fr)

- **Status**: complete; tiers tuned → profiled
- **Skipped checks**: untranslated text, character report, prose rewrite
- **Cost**: 75 LLM calls, 43.3 min; slowest: audit_translation 20.9 min, translate 13.8 min, repair_translation 2.7 min, extract_glossary 1.7 min
- **Glossary**: 44 approved entries, pair en>fr; e.g. Ada → Ada; Alice → Alice; Bill → Bill; Canary → Canari; Crab → Crabe; Dinah → Dinah; Dodo → Dodo; Duchess → Duchesse
- **Style sheet characters**: Alice (elle, vous); Bill (il, tu); Dodo (il, vous); Eaglet (il, vous); Mouse (il, vous); Pat (il, vous); White Rabbit (il, vous)
- **Audit findings** (rules): {'mistranslation': 3, 'glossary': 1}
- **Audit findings** (semantic model): {'mistranslation': 11}
  - numbers, e.g.: numeric content differs from source: The source value "nine feet" (approx. 2.74m) is trans | numeric content differs from source: The quantity of tears changes from the figurative/hyp | numeric content differs from source: SOURCE: height is "about two feet"; TRANSLATION: heig
- **Book consistency**: 3 findings; [low] The style sheet renders "I beg your pardon!" as "Je vous pri ×3
- **Review queue**: 0 segments
- **Target punctuation** (segments using each form, of 145): {'« » quotes': 113, '" straight quotes': 0, '“ ” quotes': 2, 'no-break space before ; : ! ?': 0, 'no such space before ; : ! ?': 121}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | Alice commençait à se lasser de rester assise à côté de sa sœur sur la berge, et de ne rien avoir à faire : une ou deux fois, elle avait jeté un coup d’œil dans |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | Elle y réfléchissait donc (autant qu’elle le pouvait, car la chaleur de la journée la rendait très somnolente et distraite), se demandant si le plaisir de faire |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | Il n’y avait rien d’extraordinairement remarquable là-dedans ; et Alice ne pensait pas non plus que ce soit si inhabituel d’entendre le Lapin dire à lui-même :  |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | Un instant plus tard, Alice le suivit dans le terrier, sans une seule fois se demander comment elle allait pouvoir en ressortir. |

## bench-tg3-en-ja: English → Japanese (en>ja)

- **Status**: complete; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 104 LLM calls, 38.3 min; slowest: audit_translation 15.4 min, translate 15.0 min, repair_translation 4.0 min, extract_glossary 1.7 min
- **Glossary**: 42 approved entries, pair en>ja; e.g. Ada → アダ; Alice → アリス; Bill → ビル; Canary → カナリア; caterpillar → 毛虫; Crab → カニ; Dinah → ダイナ; Dodo → ドードー
- **Style sheet characters**: Alice (彼女, —); Bill (彼, —); caterpillar (彼, —); Dinah (彼女, —); Dodo (彼, —); Eaglet (彼, —); Lory (彼, —); Mouse (彼, —)
- **Audit findings** (rules): {'glossary': 12, 'duplication': 5, 'untranslated': 2, 'mistranslation': 1}
- **Audit findings** (semantic model): {'mistranslation': 12, 'omission': 3, 'glossary': 1, 'untranslated': 1}
  - untranslated, e.g.: possible untranslated English phrase: est ma chatte | possible untranslated English word: chatte | The translation fails to render any part of the source segment.
  - numbers, e.g.: numeric content differs from source: The source value/unit 'two feet' is translated as '約6
- **Book consistency**: 2 findings; [medium] A single ellipsis … is used here, but the book uses …… (2 se ×1; [low] The style sheet renders "old fellow" as "おじいちゃん", but this t ×1
- **Review queue**: 0 segments
- **Target punctuation** (segments using each form, of 145): {'「」 quotes': 112, '" or “ ” quotes': 0, '…… ellipsis': 3, '… or ... ellipsis': 0}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | アリスは、川岸に姉と一緒に座っていることに、そして、することがないことに、少しうんざりし始めていた。時折、彼女は姉が読んでいる本を覗き込んだが、そこには絵も会話もなかった。「絵や会話がない本に、一体何の意味があるのだろうか？」とアリスは思った。 |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | そこで彼女は、自分の心の中で（暑い日だったので、彼女はとても眠くてぼんやりしていたが、できる限り）考えていた。タンポポの鎖を作る喜びは、立ち上がってタンポポを摘むという手間をかけても価値があるだろうか、と。すると突然、ピンク色の目をした白ウサギが彼女のそばを走り去った。 |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | それ自体はさほど驚くべきことではなかった。ウサギが独り言で「ああ、大変だ！ああ、大変だ！遅刻しそうだ！」と言っているのを聞くのも、アリスにはさほど不自然には思われなかった（後になって考えると、このことに驚くべきだったと気づいたが、当時はすべてごく自然に感じられた）；しかし、ウサギが実際にベストのポケットから懐中時計を取 |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | 次の瞬間、アリスもそれに続いて穴に落ちていった。彼女がどうやってそこから抜け出すのか、一度も考えなかった。 |

## bench-tg3-fr-en: French → English (fr>en)

- **Status**: complete; tiers profiled → tuned
- **Skipped checks**: untranslated text, punctuation conventions, character report
- **Cost**: 91 LLM calls, 27.6 min; slowest: translate 16.0 min, audit_translation 5.5 min, extract_glossary 2.1 min, repair_translation 1.9 min
- **Glossary**: 56 approved entries, pair fr>en; e.g. Arcadia Walker → Arcadia Walker; Dean Forsyth → Dean Forsyth; Flora Hudelson → Flora Hudelson; Francis Gordon → Francis Gordon; Jenny → Jenny; Jenny Hudelson → Jenny Hudelson; John Proth → John Proth; Kate → Kate
- **Style sheet characters**: Arcadia Walker (she, —); Dean Forsyth (he, —); Flora Hudelson (she, —); Francis Gordon (he, —); Jenny (she, —); John Proth (he, —); Kate (she, —); Loo (she, —)
- **Audit findings** (rules): {'glossary': 4, 'mistranslation': 3, 'untranslated': 1, 'duplication': 1}
- **Audit findings** (semantic model): {'mistranslation': 17, 'omission': 1}
  - untranslated, e.g.: possible untranslated French passage: je ne sais quoi
  - numbers, e.g.: numeric content differs from source: SOURCE time 'dix heures sept' (10:07) is rendered as  | numeric content differs from source: SOURCE: <I005>des cinq esprits</I005> (value: 5, unit | numeric content differs from source: The quantity of words changed from 'dix' (ten) in SOU
- **Book consistency**: 11 findings; [low] The style sheet renders "Mon oncle" as "Uncle", but this tra ×7; [low] The style sheet renders "faubourg de Wilcox" as "Wilcox subu ×2; [low] The style sheet renders "Au nom de la loi" as "In the name o ×1; [low] The style sheet renders "je vous déclare unis" as "I declare ×1
- **Review queue**: 0 segments
- **Target punctuation** (segments using each form, of 340): {'“ ” quotes': 127, '" straight quotes': 0}

| source | translation |
|---|---|
| I DANS LEQUEL LE JUGE JOHN PROTH REMPLIT UN DES PLUS AGRÉABLES DEVOIRS DE SA CHARGE AVANT DE RETOURNER A SON JARDIN. | I IN WHICH JUDGE JOHN PROTH PERFORMS ONE OF THE MOST PLEASANT DUTIES OF HIS OFFICE BEFORE RETURNING TO HIS GARDEN. |
| Il n’y a aucun motif pour cacher aux lecteurs que la ville dans laquelle commence cette histoire singulière est située en Virginie, États-Unis d’Amérique. S’ils | There is no reason to conceal from the readers that the city in which this singular story begins is located in Virginia, United States of America. If they are s |
| Cette année-là, le 12 mars, dans la matinée, ceux des habitants de Whaston qui traversèrent Exeter street au moment convenable purent apercevoir un élégant cava | That year, on March 12, in the morning, those of the inhabitants of Whaston who crossed Exeter Street at the appropriate time could see an elegant horseman ridi |
| Ce cavalier, de pur type yankee, type qui n’est point exempt d’une originale distinction, ne devait pas avoir plus de trente ans. Il était d’une taille au-dessu | This horseman, of pure Yankee type, a type which is not without a certain original distinction, could not have been more than thirty years old. He was of above  |

## bench-tg3-es-en: Spanish → English (es>en)

- **Status**: paused; tiers profiled → tuned
- **Skipped checks**: untranslated text, punctuation conventions, character report
- **Cost**: 141 LLM calls, 44.7 min; slowest: audit_translation 18.0 min, translate 15.2 min, extract_glossary 4.2 min, repair_translation 2.7 min
- **Glossary**: 168 approved entries, pair es>en; e.g. Albert → Albert; Alburquerque → Albuquerque; Alejandro Miquis → Alejandro Miquis; Arnaiz → Arnaiz; Ayún → Ayún; Baldomerito → Baldomerito; Baldomero → Baldomero; Barbarita → Barbarita
- **Style sheet characters**: Arnaiz (he, —); Baldomero (he, —); Barbarita (she, —); doña Asunción Trujillo (she, —); Gumersindo Arnaiz (he, —); Gustavito (he, —); Isabel Cordero (she, —); Jacinto María Villalonga (he, —)
- **Audit findings** (rules): {'glossary': 13, 'mistranslation': 8}
- **Audit findings** (semantic model): {'mistranslation': 14}
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


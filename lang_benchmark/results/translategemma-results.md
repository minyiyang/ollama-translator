# Language-profile benchmarks

## bench-tg-de-en: German → English (de>en)

- **Status**: paused; tiers profiled → tuned
- **Skipped checks**: untranslated text, punctuation conventions, character report
- **Cost**: 71 LLM calls, 39.5 min; slowest: audit_translation 13.9 min, repair_translation 9.0 min, translate 7.4 min, repair_review 3.5 min
- **Glossary**: 4 approved entries, pair de>en; e.g. Gregor → Gregor; Mutter → Mother; Vater → Father; Prokurist → authorized signatory
- **Style sheet characters**: Gregor (he, —); Gregor Samsa (he, —); Mutter (she, —); Prokurist (he, —); Vater (he, —)
- **Audit findings** (rules): {'glossary': 34, 'duplication': 11, 'mistranslation': 4, 'omission': 1}
- **Audit findings** (semantic model): {'mistranslation': 1}
  - numbers, e.g.: numeric content differs from source: The translation is completely unrelated to the source | numeric content differs from source: SOURCE: "um acht Uhr" (at eight o'clock); TRANSLATION | numeric content differs from source: The translation is a complete hallucination/mismatch;
- **Book consistency**: 7 findings; [low] The style sheet renders "das Geschäft" as "the firm", but th ×3; [low] The style sheet renders "Herr Samsa" as "Mr. Samsa", but thi ×2; [low] The style sheet renders "um Gottes willen" as "for God's sak ×2
- **Review queue**: 4 segments
- **Target punctuation** (segments using each form, of 31): {'“ ” quotes': 21, '" straight quotes': 0}

| source | translation |
|---|---|
| Als Gregor Samsa eines Morgens aus unruhigen Träumen erwachte, fand er sich in seinem Bett zu einem ungeheueren Ungeziefer verwandelt. Er lag auf seinem panzera | One morning, as Gregor Samsa awoke from uneasy dreams, he found himself transformed in his bed into a monstrous vermin. He lay on his back, his hard, armored sh |
| »Was ist mit mir geschehen?« dachte er. Es war kein Traum. Sein Zimmer, ein richtiges, nur etwas zu kleines Menschenzimmer, lag ruhig zwischen den vier wohlbeka | “What has happened to me?” he thought. It was no dream. His room, a proper, though somewhat too small room for a man, lay quietly between the four familiar wall |
| Gregors Blick richtete sich dann zum Fenster, und das trübe Wetter – man hörte Regentropfen auf das Fensterblech aufschlagen – machte ihn ganz melancholisch. »W | Gregor’s gaze then fell on the window, and the gloomy weather – he could hear raindrops pattering on the window sill – made him feel quite melancholy. “What if  |
| »Ach Gott,« dachte er, »was für einen anstrengenden Beruf habe ich gewählt! Tag aus, Tag ein auf der Reise. Die geschäftlichen Aufregungen sind viel größer, als | “Oh God,” he thought, “what a strenuous job I have chosen! Day in, day out on the road. The business worries are much greater than at home, and besides, I am bu |

## bench-tg-en-ko: English → Korean (en>ko)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 128 LLM calls, 96.2 min; slowest: translate 37.1 min, audit_translation 26.7 min, repair_review 13.2 min, repair_translation 11.2 min
- **Glossary**: 43 approved entries, pair en>ko; e.g. Ada → 아다; Alice → 앨리스; Bill → 빌; caterpillar → 애벌레; Crab → 게; Dinah → 디나; Dodo → 도도새; Duchess → 공작부인
- **Style sheet characters**: Alice (그녀, —); Bill (그, —); caterpillar (그, —); Crab (그, —); Dinah (그녀, —); Dodo (그, —); Duck (그, —); Eaglet (그, —)
- **Audit findings** (rules): {'glossary': 11, 'duplication': 5, 'addition': 3, 'mistranslation': 2, 'ai_style': 2, 'untranslated': 1}
- **Audit findings** (semantic model): {'mistranslation': 18, 'omission': 2, 'untranslated': 1}
  - untranslated, e.g.: text in a script neither language uses: 溺死 | The translation fails to render the specific names and entities mentioned in the source.
  - numbers, e.g.: numeric content differs from source: SOURCE: 'about two feet high' (value: 2, unit: feet,  | numeric content differs from source: The source ratio "a thousand times as large as" (appr
- **Book consistency**: 8 findings; [low] The style sheet renders "I beg your pardon!" as "뭐라고 하셨죠?",  ×3; [low] The style sheet renders "yer honour" as "나으리", but this tran ×3; [low] The style sheet renders "old fellow" as "이보게", but this tran ×2
- **Review queue**: 3 segments
- **Target punctuation** (segments using each form, of 145): {'“ ” quotes': 113, '" straight quotes': 0, '「」 quotes': 0, '…… ellipsis': 0}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | 앨리스는 강둑에 앉아 있는 여동생 옆에 앉아 있는 것이 점점 지루해지고, 할 일이 없어지자, 여동생이 읽고 있는 책을 흘끗 보았지만, 그림이나 대화가 없어서 “그림이나 대화가 없는 책은 무슨 소용일까?”라고 생각했다. |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | 그래서 앨리스는 자신의 마음속으로 생각했다(더운 날씨 때문에 앨리스는 매우 졸리고 어리석다고 느꼈지만), 데이지 꽃으로 목걸이를 만드는 즐거움이 데이지를 꺾어오는 수고로움을 감수할 만한 가치가 있을지, 그러던 중 갑자기 분홍색 눈을 가진 <000>흰 토끼</000>가 그녀 옆을 빠르게  |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | 그것은 매우 특별할 것이 없었고, 앨리스는 토끼가 스스로에게 “아이고! 아이고! 늦겠어!”라고 말하는 것을 듣는 것도 매우 이상하게 생각하지 않았다. (나중에 생각해보니, 그때에 그 점이 놀라웠어야 했다는 생각이 들었지만, 당시에는 모든 것이 꽤 자연스러웠다.) 그러나 토끼가 실제로 베 |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | 잠시 후 앨리스도 토끼를 따라 굴 속으로 뛰어들었고, 다시 어떻게 나올 수 있을지 전혀 생각하지 않았다. |

## bench-tg-en-de: English → German (en>de)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: untranslated text, character report, prose rewrite
- **Cost**: 92 LLM calls, 86.8 min; slowest: audit_translation 28.3 min, translate 19.7 min, resolve_glossary 15.6 min, repair_translation 9.9 min
- **Glossary**: 38 approved entries, pair en>de; e.g. Ada → Ada; Alice → Alice; Bill → Bill; Canary → Kanarienvogel; Dinah → Dinah; Dodo → Dodo; Duchess → Herzogin; Duck → Ente
- **Style sheet characters**: Alice (sie, du); Bill (er, du); Canary (er, Sie); caterpillar (er, Sie); Crab (sie, du); Dinah (sie, du); Dodo (er, Sie); Duchess (sie, Sie)
- **Audit findings** (rules): {'glossary': 16, 'mistranslation': 4, 'duplication': 4, 'addition': 1}
- **Audit findings** (semantic model): {'mistranslation': 7}
  - numbers, e.g.: numeric content differs from source: The quantity 'more than nine feet high' (value: more  | numeric content differs from source: The depth of the pool is changed from 'four inches' ( | numeric content differs from source: The translation introduces a completely new quantity-
- **Book consistency**: 2 findings; [medium] An English closing quotation mark ” is used here, but the bo ×2
- **Review queue**: 1 segments
- **Target punctuation** (segments using each form, of 145): {'„ “ quotes': 114, '» « quotes': 0, '” English closing quote': 0, '" straight quotes': 0, '– Gedankenstrich': 17}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | Alice begann, das Sitzen neben ihrer Schwester am Ufer ziemlich lästig zu finden, und auch, nichts zu tun zu haben: Ein- oder zweimal hatte sie verstohlen in da |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | So überlegte sie in ihrem stillen Inneren (so gut sie konnte, denn die Hitze des Tages machte sie sehr schläfrig und träge), ob die Freude, eine Gänseblümchenke |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | Es war nichts außergewöhnliches daran; und Alice fand es auch nicht besonders merkwürdig, den Hasen zu hören, wie er zu sich selbst murmelte: „Oh je! Oh je! Ich |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | Im nächsten Augenblick fiel Alice ihm nach, ohne auch nur einen Augenblick darüber nachzudenken, wie sie jemals wieder herauskommen sollte. |

## bench-tg-en-es: English → Spanish (en>es)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: untranslated text, character report, prose rewrite
- **Cost**: 102 LLM calls, 30.7 min; slowest: translate 16.7 min, audit_translation 6.0 min, repair_translation 3.4 min, extract_glossary 1.4 min
- **Glossary**: 39 approved entries, pair en>es; e.g. Ada → Ada; Alice → Alicia; Bill → Bill; Canary → Canario; Crab → Cangrejo; Dinah → Dinah; Dodo → Dodo; Duchess → Duquesa
- **Style sheet characters**: Alice (ella, usted); Bill (él, tú); Caterpillar (él, usted); Crab (él, usted); Dinah (ella, tú); Dodo (él, usted); Duck (él, usted); Eaglet (él, usted)
- **Audit findings** (rules): {'glossary': 11, 'mistranslation': 5, 'omission': 1}
- **Audit findings** (semantic model): {'mistranslation': 10, 'glossary': 2, 'omission': 2}
  - numbers, e.g.: numeric content differs from source: The source value 'nine feet' is converted to 'dos met | numeric content differs from source: The depth of the pool is translated as 'unos diez cen | numeric content differs from source: SOURCE: 'about two feet high' (value: 2, unit: feet, 
- **Book consistency**: 5 findings; [medium] An exclamation here has no opening ¡, but the book writes th ×2; [medium] A question here has no opening ¿, but the book writes them ( ×1; [low] The style sheet renders "Do cats eat bats?" as "¿Comen los g ×1; [medium] Repeated quoted line "I beg your pardon" is rendered "Le pid ×1
- **Review queue**: 5 segments
- **Target punctuation** (segments using each form, of 145): {'« » quotes': 15, '“ ” quotes': 104, '" straight quotes': 1, '¿ opening': 40, '? without ¿ in the segment': 0, '— raya dialogue': 5}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | Alicia comenzaba a sentirse muy cansada de estar sentada junto a su hermana, al borde del río, y de no tener nada que hacer: una o dos veces había espiado el li |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | Así que lo consideraba en su mente (tanto como podía, pues el calor del día la hacía sentir muy somnolienta y aturdida), si el placer de tejer una guirnalda de  |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | No había nada tan remarcable en ello; ni tampoco Alicia pensó que fuera algo tan insólito oír al Conejo decir para sí mismo: “¡Ay, Dios mío! ¡Ay, Dios mío! ¡Lle |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | En un instante, Alicia lo siguió, sin considerar ni por un momento cómo iba a salir de allí. |

## bench-tg-en-fr: English → French (en>fr)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: untranslated text, character report, prose rewrite
- **Cost**: 95 LLM calls, 34.7 min; slowest: translate 17.9 min, audit_translation 8.5 min, repair_translation 3.4 min, extract_glossary 1.4 min
- **Glossary**: 45 approved entries, pair en>fr; e.g. Ada → Ada; Alice → Alice; Bill → Bill; Canary → Canari; caterpillar → chenille; Crab → Crabe; Dinah → Dinah; Dodo → Dodo
- **Style sheet characters**: Alice (elle, vous); Bill (il, tu); caterpillar (il, vous); Crab (il, vous); Dinah (elle, tu); Dodo (il, vous); Duck (il, vous); Eaglet (il, vous)
- **Audit findings** (rules): {'glossary': 21, 'duplication': 4, 'mistranslation': 3, 'addition': 1}
- **Audit findings** (semantic model): {'mistranslation': 5, 'glossary': 2, 'structure': 1, 'omission': 1}
  - numbers, e.g.: numeric content differs from source: The quantity of tears is changed from 'gallons' (SOUR | numeric content differs from source: The source value 'two feet' (approx. 60.96 cm) is tra | numeric content differs from source: The translation introduces several quantity-bearing f
- **Book consistency**: 7 findings; [low] The style sheet renders "Oh dear!" as "Ma parole !", but thi ×4; [low] The style sheet renders "I beg your pardon!" as "Je vous pri ×3
- **Review queue**: 1 segments
- **Target punctuation** (segments using each form, of 145): {'« » quotes': 114, '" straight quotes': 0, '“ ” quotes': 0, 'no-break space before ; : ! ?': 0, 'no such space before ; : ! ?': 119}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | Alice commençait à se lasser de rester assise près de sa sœur, sur la rive, et de ne rien avoir à faire : une ou deux fois, elle avait jeté un coup d’œil dans l |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | Elle y réfléchissait donc (autant qu’elle le pouvait, car la chaleur de la journée la rendait très somnolente et distraite), se demandant si le plaisir de faire |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | Il n’y avait rien de très remarquable à cela ; et Alice ne trouvait pas non plus si très étrange d’entendre le Lapin se dire à lui-même : « Ma parole ! Ma parol |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | Un instant plus tard, Alice le suivit dans le terrier, sans se soucier une seule seconde de savoir comment elle allait pouvoir en ressortir. |

## bench-tg-en-ja: English → Japanese (en>ja)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 139 LLM calls, 57.8 min; slowest: audit_translation 26.6 min, translate 21.0 min, repair_translation 3.4 min, rescue_translation 1.7 min
- **Glossary**: 42 approved entries, pair en>ja; e.g. Ada → アダ; Alice → アリス; Bill → ビル; Canary → カナリア; caterpillar → 毛虫; Crab → カニ; Dinah → ダイナ; Dodo → ドードー
- **Style sheet characters**: Alice (彼女, —); Bill (彼, —); caterpillar (彼, —); Crab (彼, —); Dinah (彼女, —); Dodo (彼, —); Duck (彼, —); Eaglet (彼, —)
- **Audit findings** (rules): {'glossary': 16, 'duplication': 8, 'untranslated': 2, 'addition': 2, 'mistranslation': 1, 'omission': 1}
- **Audit findings** (semantic model): {'mistranslation': 11, 'untranslated': 2, 'structure': 1, 'omission': 1, 'glossary': 1}
  - untranslated, e.g.: possible untranslated English phrase: est ma chatte | possible untranslated English word: chatte | The translation provides text that does not correspond to the source segment provided. | The translation provides text that does not correspond to the source provided in the segme
  - numbers, e.g.: numeric content differs from source: The translation contains entirely different content f
- **Book consistency**: 2 findings; [low] The style sheet renders "old fellow" as "君", but this transl ×2
- **Review queue**: 2 segments
- **Target punctuation** (segments using each form, of 145): {'「」 quotes': 112, '" or “ ” quotes': 0, '…… ellipsis': 1, '… or ... ellipsis': 0}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | アリスは、姉のそばで川岸に座り、何もすることがないことに、だんだんうんざりし始めていた。姉が読んでいる本を、一度や二度、のぞいてみたが、そこには絵も会話もなかった。「絵も会話もない本に、いったい何の意味があるのかしら」とアリスは思った。 |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | そこで彼女は、自分の頭の中で（暑い日差しで、とても眠く、ぼんやりしていたので、できる限りの範囲で）、マリーゴールドの冠を作る楽しみが、起きてマリーゴールドを摘む手間に見合うかどうかを考えていたところ、突然、ピンク色の目をした白ウサギが、彼女のすぐそばを走っていった。 |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | それ自体は、特に驚くべきことではなかった。ウサギが独り言で「あらまあ、あらまあ、遅れるわ！」と言っているのを聞いたことも、特におかしいとは思わなかった（後で思い返すと、そこで驚くべきだったのに、と気づいたが、当時はすべてごく自然に思えた）。しかし、ウサギが実際にベストのポケットから懐中時計を取り出し、それを見て、急いで |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | 次の瞬間、アリスは、どうやってまた外に出るのか、一度も考えずに、ウサギのあとを追って落ちていった。 |

## bench-tg-fr-en: French → English (fr>en)

- **Status**: paused; tiers profiled → tuned
- **Skipped checks**: untranslated text, punctuation conventions, character report
- **Cost**: 187 LLM calls, 119.1 min; slowest: resolve_glossary 48.2 min, translate 36.7 min, approve_glossary 13.3 min, audit_translation 10.2 min
- **Glossary**: 56 approved entries, pair fr>en; e.g. Arcadia Walker → Arcadia Walker; Dean Forsyth → Dean Forsyth; Flora Hudelson → Flora Hudelson; Francis Gordon → Francis Gordon; Jenny → Jenny; Jenny Hudelson → Jenny Hudelson; John Proth → John Proth; Kate → Kate
- **Style sheet characters**: Arcadia Walker (she, —); Dean Forsyth (he, —); Flora Hudelson (she, —); Francis Gordon (he, —); Jenny (she, —); John Proth (he, —); Kate (she, —); Loo (she, —)
- **Audit findings** (rules): {'glossary': 111, 'omission': 16, 'duplication': 10, 'addition': 9, 'mistranslation': 5, 'untranslated': 2}
- **Audit findings** (semantic model): {'mistranslation': 7, 'untranslated': 5, 'omission': 2}
  - untranslated, e.g.: The translation fails to render the source text, replacing it with unrelated dialogue/acti | possible untranslated French passage: je ne sais quoi | The translation introduces an entirely new narrative block (biography of Arcadia Walker) t | The translation provides an entirely different text (hallucinated or shifted from another  | The translation introduces a vast amount of unsupported information (dates, times, locatio
  - numbers, e.g.: numeric content differs from source: SOURCE quantity: value 'sept', unit 'minutes', subjec | numeric content differs from source: The translation contains no quantity-bearing facts, w | numeric content differs from source: The source contains a specific quantity ('un billet d
- **Book consistency**: 65 findings; [low] The style sheet renders "Mr John Proth" as "Mr. John Proth", ×14; [low] The style sheet renders "miss Arcadia Walker" as "Miss Arcad ×14; [low] The style sheet renders "Mr Seth Stanfort" as "Mr. Seth Stan ×11; [low] The style sheet renders "Mon oncle" as "Uncle", but this tra ×7; [low] The style sheet renders "Exeter street" as "Exeter Street",  ×6
- **Review queue**: 2 segments
- **Target punctuation** (segments using each form, of 340): {'“ ” quotes': 122, '" straight quotes': 0}

| source | translation |
|---|---|
| I DANS LEQUEL LE JUGE JOHN PROTH REMPLIT UN DES PLUS AGRÉABLES DEVOIRS DE SA CHARGE AVANT DE RETOURNER A SON JARDIN. | I IN WHICH JUDGE JOHN PROTH PERFORMS ONE OF THE MOST PLEASANT DUTIES OF HIS OFFICE BEFORE RETURNING TO HIS GARDEN. |
| Il n’y a aucun motif pour cacher aux lecteurs que la ville dans laquelle commence cette histoire singulière est située en Virginie, États-Unis d’Amérique. S’ils | There is no reason to conceal from the readers that the city in which this singular story begins is located in Virginia, United States of America. If they so wi |
| Cette année-là, le 12 mars, dans la matinée, ceux des habitants de Whaston qui traversèrent Exeter street au moment convenable purent apercevoir un élégant cava | That year, on March 12, in the morning, those of the inhabitants of Whaston who passed through Exeter Street at the appropriate time could see an elegant horsem |
| Ce cavalier, de pur type yankee, type qui n’est point exempt d’une originale distinction, ne devait pas avoir plus de trente ans. Il était d’une taille au-dessu | This horseman, of pure Yankee type, a type that is not without a certain original distinction, could not have been more than thirty years old. He was above aver |

## bench-tg-es-en: Spanish → English (es>en)

- **Status**: paused; tiers profiled → tuned
- **Skipped checks**: untranslated text, punctuation conventions, character report
- **Cost**: 146 LLM calls, 80.2 min; slowest: translate 44.3 min, audit_translation 16.4 min, repair_translation 13.4 min, approve_glossary 2.5 min
- **Glossary**: 0 approved entries, pair es>en; e.g. 
- **Style sheet characters**: Albert (he, —); Alejandro Miquis (he, —); Ayún (he, —); Baldomero (he, —); Barbarita (she, —); Bonifacio Arnaiz (he, —); Bravo Murillo (he, —); Candelaria (she, —)
- **Audit findings** (rules): {'duplication': 24, 'mistranslation': 18, 'addition': 3}
- **Audit findings** (semantic model): {'mistranslation': 8, 'untranslated': 1}
  - untranslated, e.g.: The translation provides a completely unrelated text instead of translating the source. Th
  - numbers, e.g.: numeric content differs from source: The source idiom "daba quince y raya" (meaning to giv | numeric content differs from source: The translation omits the specific quantity of Juanit | numeric content differs from source: The translation is a complete hallucination/substitut
- **Book consistency**: 13 findings; [low] The style sheet renders "la calle de Postas" as "Postas Stre ×3; [low] The style sheet renders "Juanito Santa Cruz" as "Juanito San ×2; [low] The style sheet renders "pañuelos de Manila" as "Manila hand ×2; [low] The style sheet renders "el gordo Arnaiz" as "the fat Arnaiz ×2; [low] The style sheet renders "la calle de Tintoreros" as "Dyers'  ×1
- **Review queue**: 1 segments
- **Target punctuation** (segments using each form, of 88): {'“ ” quotes': 15, '" straight quotes': 7}

| source | translation |
|---|---|
| Las noticias más remotas que tengo de la persona que lleva este nombre me las ha dado Jacinto María Villalonga, y alcanzan al tiempo en que este amigo mío y el  | The most remote news I have of the person who bears this name comes from Jacinto María Villalonga, and it dates back to the time when this friend of mine, and t |
| ¡Ay!, el susto que se llevaron D. Baldomero Santa Cruz y Barbarita no es para contado. ¡Qué noche de angustia la del 10 al 11! Ambos creían no volver a ver a su | Oh, the fright that Don Baldomero Santa Cruz and Barbarita experienced is beyond description. What a night of anguish from the 10th to the 11th! Both believed t |
| Cuando el niño estudiaba los últimos años de su carrera, verificose en él uno de esos cambiazos críticos que tan comunes son en la edad juvenil. De travieso y a | When the boy was studying the final years of his degree, one of those critical changes occurred in him that are so common in youth. From being mischievous and b |
| Todos los dineros que su papá le daba, dejábalos Juanito en casa de Bailly-Baillière, a cuenta de los libros que iba tomando. Refiere Villalonga que un día fue  | All the money his father gave him, Juanito left at Bailly-Baillière’s, on account of the books he was taking. Villalonga recounts that one day Barbarita went to |


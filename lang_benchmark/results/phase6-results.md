# Language-profile benchmarks

## bench-lang-ko-zh: Korean → Simplified Chinese (ko>zh)

- **Status**: complete; tiers profiled → tuned
- **Skipped checks**: none
- **Cost**: 41 LLM calls, 40.9 min; slowest: translate 24.8 min, audit_translation 11.4 min, extract_glossary 1.9 min, resolve_glossary 1.5 min
- **Glossary**: 83 approved entries, pair ko>zh; e.g. 김첨지 → 金添智; 치삼이 → 智三; 현진건 → 玄镇健; 남대문 → 南大门; 동소문 → 东小门; 인사동 → 仁寺洞; 인천 → 仁川; 정거장 → 车站
- **Style sheet characters**: 개똥이 (它, 你); 김첨지 (他, 你); 아내 (她, 你); 치삼이 (他, 你)
- **Audit findings** (rules): {'glossary': 29}
- **Audit findings** (semantic model): {'mistranslation': 7}
- **Book consistency**: no findings
- **Review queue**: 0 segments
- **Target punctuation** (segments using each form, of 103): {'“ ” quotes': 65, '「」 quotes': 0, '" straight quotes': 0, '—— paired dash': 6, '— single dash': 0}

| source | translation |
|---|---|
| 이날이야말로 동소문 안에서 인력거꾼 노릇을 하는 김첨지에게는 오래간만에도 닥친 운수 좋은 날이었다. 문안에(거기도 문밖은 아니지만) 들어간답시는 앞집 마마님을 전찻길까지 모셔다 드린 것을 비롯으로 행여나 손님이있을까 하고 정류장에서 어정어정하며 내리는 사람 하나하나에게 거의 비는듯한 눈 | 这一天，对于在东小门里拉人力车的金添智来说，确是许久未遇的走运日子。那位声称要进里院（虽说里院也不算外头）的前院太太，先是被送到了电车站，接着便在车站旁徘徊不定，生怕错过客人，对每一个下车的人几乎都投去恳求般的目光。终于，一位像是教员模样的西装客，答应让他送到东光学校。 |
| 첫 번에 삼십전 , 둘째 번에 오십전 - 아침 댓바람에 그리 흉치 않은 일이었다. 그야말로 재수가 옴붙어서 근 열흘 동안 돈 구경도 못한 김첨지는 십전짜리 백동화 서 푼, 또는 다섯 푼이 찰깍 하고 손바닥에 떨어질 제 거의눈물을 흘릴 만큼 기뻤었다. 더구나 이날 이때에 이 팔십 전이라는 | 头一趟三十钱，第二趟五十钱——大清早便得了这样不算太差的进项。那真是时来运转，近十天连钱影儿都没见着的金添智，每当十钱一枚的白铜币或五钱、三钱叮当落在掌心时，欢喜得几乎要掉下泪来。更何况，此刻这八十钱对他而言是何等有用。不仅能给干渴的喉咙润上一口米酒，更能为病中的妻子买上一碗牛骨汤。 |
| 그의 아내가 기침으로 쿨룩거리기는 벌써 달포가 넘었다. 조밥도 굶기를먹다시피 하는 형편이니 물론 약 한 첩 써본 일이 없다. 구태여 쓰려면 못쓸 바도 아니로되 그는 병이란 놈에게 약을 주어 보내면 재미를 붙여서 자꾸 온다는 자기의 신조(信條)에 어디까지 충실하였다. 따라서 의사에게 보인 | 他的妻子咳嗽不止，已有一个多月了。糙米饭也是饥一顿饱一顿，自然没吃过一副药。若非要吃，倒也不是吃不起，只是他信奉一条信条：病这东西，一旦喂了药，便会缠上你，没完没了。因此从未请医生看过，究竟是什么病也无从知晓，但看她躺下便再也起不来，似乎病得不轻。病势恶化，起因是十天前吃糙米饭积食了。那时金添智难得挣了点钱，买了一升小 |
| “에이, 오라질년, 조랑복은 할 수가 없어, 못 먹어 병, 먹어서 병! 어쩌란 말이야! 왜 눈을 바루 뜨지 못해!”하고 앓는 이의 뺨을 한 번 후려갈겼다. 흡뜬 눈은 조금 바루어졌건만 이슬이 맺히었다. 김첨지의 눈시울도 뜨끈뜨끈하였다. | “呸，该死的婆娘，想享福没门儿！不吃是病，吃了也是病！到底要我怎样！怎么连眼睛都睁不开了！”说着，狠狠扇了病中人的脸颊一巴掌。翻着的白眼稍微正了一些，却蒙上了一层水雾。金添智的眼眶也热辣辣的。 |

## bench-lang-de-en: German → English (de>en)

- **Status**: complete; tiers profiled → tuned
- **Skipped checks**: untranslated text, punctuation conventions, character report
- **Cost**: 44 LLM calls, 12.7 min; slowest: audit_translation 8.5 min, translate 1.9 min, extract_glossary 0.8 min, repair_translation 0.7 min
- **Glossary**: 4 approved entries, pair de>en; e.g. Gregor → Gregor; Mutter → Mother; Vater → Father; Prokurist → Procurator
- **Style sheet characters**: Gregor (he, —); Gregor Samsa (he, —); Mutter (she, —); Prokurist (he, —); Vater (he, —)
- **Audit findings** (rules): {'glossary': 15}
- **Audit findings** (semantic model): {'mistranslation': 11, 'glossary': 1}
- **Book consistency**: no findings
- **Review queue**: 0 segments
- **Target punctuation** (segments using each form, of 31): {'“ ” quotes': 20, '" straight quotes': 0}

| source | translation |
|---|---|
| Als Gregor Samsa eines Morgens aus unruhigen Träumen erwachte, fand er sich in seinem Bett zu einem ungeheueren Ungeziefer verwandelt. Er lag auf seinem panzera | When Gregor Samsa awoke one morning from troubled dreams, he found himself transformed in his bed into a monstrous vermin. He lay on his back, which was hard as |
| »Was ist mit mir geschehen?« dachte er. Es war kein Traum. Sein Zimmer, ein richtiges, nur etwas zu kleines Menschenzimmer, lag ruhig zwischen den vier wohlbeka | “What has happened to me?” he thought. It was no dream. His room, a proper room, only somewhat too small for a human being, lay quiet between its four familiar  |
| Gregors Blick richtete sich dann zum Fenster, und das trübe Wetter – man hörte Regentropfen auf das Fensterblech aufschlagen – machte ihn ganz melancholisch. »W | Gregor’s gaze then turned to the window, and the gloomy weather—he could hear raindrops striking the window screen—made him quite melancholy. “What if I were to |
| »Ach Gott,« dachte er, »was für einen anstrengenden Beruf habe ich gewählt! Tag aus, Tag ein auf der Reise. Die geschäftlichen Aufregungen sind viel größer, als | “Oh God,” he thought, “what a strenuous profession I have chosen! Day in, day out on the road. The business stresses are far greater than in the actual business |

## bench-lang-de-zh: German → Simplified Chinese (de>zh)

- **Status**: paused; tiers profiled → tuned
- **Skipped checks**: none
- **Cost**: 35 LLM calls, 12.8 min; slowest: audit_translation 8.6 min, translate 1.8 min, extract_glossary 0.9 min, approve_glossary 0.3 min
- **Glossary**: 4 approved entries, pair de>zh; e.g. Gregor → 格里高尔; Mutter → 母亲; Prokurist → 副经理; Vater → 父亲
- **Style sheet characters**: Gregor (他, 你); Grete (她, 你); Mutter (她, 您); Prokurist (他, 您); Vater (他, 你)
- **Audit findings** (rules): {'glossary': 16}
- **Audit findings** (semantic model): {'mistranslation': 2}
- **Book consistency**: 3 findings; [low] The style sheet renders "Geschäft" as "生意", but this transla ×3
- **Review queue**: 1 segments
- **Target punctuation** (segments using each form, of 31): {'“ ” quotes': 20, '「」 quotes': 0, '" straight quotes': 0, '—— paired dash': 17, '— single dash': 0}

| source | translation |
|---|---|
| Als Gregor Samsa eines Morgens aus unruhigen Träumen erwachte, fand er sich in seinem Bett zu einem ungeheueren Ungeziefer verwandelt. Er lag auf seinem panzera | 当格里高尔·萨姆沙在一个早晨从纷乱的梦境中醒来时，发现自己躺在床上，变成了一只巨大的甲虫。他仰面躺着，背脊坚硬如甲壳，微微抬起头，便看见自己拱起的棕色腹部，被一道道弧形的硬板分割开来；被子在那高度上勉强维持着，随时准备彻底滑落。他那许多条腿——与身体其余部分相比显得可怜地细弱——在他眼前无助地颤动。 |
| »Was ist mit mir geschehen?« dachte er. Es war kein Traum. Sein Zimmer, ein richtiges, nur etwas zu kleines Menschenzimmer, lag ruhig zwischen den vier wohlbeka | “我身上发生了什么？”他想。这不是梦。他的房间，一个真正的、只是略显狭小的人间房间，静静地躺在四面熟悉的墙壁之间。桌子上铺着一套拆开的布料样品——萨姆沙是个推销员——上方挂着一幅画，那是他不久前从一本画报上剪下来，装进一个漂亮的镀金相框里的。画中是一位女士，戴着皮帽，围着皮围巾，端坐着，将一只厚重的皮手笼——她的整条前 |
| Gregors Blick richtete sich dann zum Fenster, und das trübe Wetter – man hörte Regentropfen auf das Fensterblech aufschlagen – machte ihn ganz melancholisch. »W | 格里高尔的目光随后转向窗户，那阴沉的天气——听得见雨滴打在窗板上的声音——让他感到一阵忧郁。“要是我再睡一会儿，忘掉所有这些荒唐事，该多好啊，”他想，但这完全行不通，因为他习惯右侧卧，可在他目前的状态下，根本无法摆出这个姿势。无论他用多大的力气向右侧翻去，总是又晃回仰卧的姿势。他试了大约一百次，闭上眼睛，不愿看那些乱动 |
| »Ach Gott,« dachte er, »was für einen anstrengenden Beruf habe ich gewählt! Tag aus, Tag ein auf der Reise. Die geschäftlichen Aufregungen sind viel größer, als | “天哪，”他想，“我选了份多么累人的工作！日复一日地奔波。生意上的紧张比在家里的正经生意大得多，而且还得忍受这种旅行的折磨：担心火车接驳，吃不规律、质量差的饭，以及那些永远在变、从不持久、也从不变得亲切的人际关系。愿魔鬼把这些统统带走！”他感到腹部上方有点痒；他在背上慢慢挪向床柱，以便更好地抬起头；找到了那发痒的地方， |

## bench-lang-en-es: English → Spanish (en>es)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: untranslated text, character report, prose rewrite
- **Cost**: 122 LLM calls, 26.9 min; slowest: audit_translation 16.6 min, translate 3.8 min, repair_translation 2.3 min, extract_glossary 1.4 min
- **Glossary**: 48 approved entries, pair en>es; e.g. Ada → Ada; Alice → Alicia; Bill → Bill; Canary → Canario; Crab → Cangrejo; Dinah → Dinah; Dodo → Dodo; Duck → Pato
- **Style sheet characters**: Alice (ella, tú); Bill (él, tú); Caterpillar (él, tú); Crab (él, tú); Dodo (él, tú); Duck (él, tú); Eaglet (él, tú); Fury (él, tú)
- **Audit findings** (rules): {'glossary': 11}
- **Audit findings** (semantic model): {'mistranslation': 16, 'glossary': 2}
- **Book consistency**: 25 findings; [medium] An exclamation here has no opening ¡, but the book writes th ×17; [medium] A question here has no opening ¿, but the book writes them ( ×5; [low] The style sheet renders "Oh dear!" as "¡Ay, Dios mío!", but  ×3
- **Review queue**: 14 segments
- **Target punctuation** (segments using each form, of 145): {'« » quotes': 114, '“ ” quotes': 1, '" straight quotes': 0, '¿ opening': 43, '? without ¿ in the segment': 0, '— raya dialogue': 15}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | Alicia empezaba a sentirse muy cansada de estar sentada junto a su hermana en la orilla, y de no tener nada que hacer: una o dos veces había asomado la vista al |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | Así que estaba considerando en su mente (lo mejor que podía, pues el día caluroso la hacía sentir muy somnolienta y estúpida) si el placer de hacer una guirnald |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | No había nada de muy extraordinario en ello; ni Alicia pensó que fuera muy fuera de lo común oír al Conejo decirse a sí mismo: «¡Ay, Dios mío! ¡Ay, Dios mío! ¡L |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | En un instante Alicia bajó tras él, sin considerar ni una vez cómo en el mundo iba a salir de nuevo. |

## bench-lang-en-de: English → German (en>de)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: untranslated text, character report, prose rewrite
- **Cost**: 154 LLM calls, 59.5 min; slowest: audit_translation 47.1 min, translate 4.9 min, repair_translation 2.9 min, extract_glossary 1.6 min
- **Glossary**: 42 approved entries, pair en>de; e.g. Ada → Ada; Alice → Alice; Bill → Bill; Canary → Kanarienvogel; Crab → Krabbe; Dinah → Dinah; Dodo → Dodo; Duchess → Herzogin
- **Style sheet characters**: Alice (sie, du); Bill (er, du); caterpillar (es, Sie); Crab (sie, Sie); Dinah (sie, Sie); Dodo (es, Sie); Duck (sie, Sie); Eaglet (es, Sie)
- **Audit findings** (rules): {'untranslated': 14, 'glossary': 7, 'mistranslation': 2}
- **Audit findings** (semantic model): {'mistranslation': 13, 'untranslated': 6, 'glossary': 1}
  - untranslated, e.g.: The dialogue of the second speaker is left entirely untranslated in the target language, d | possible untranslated English passage: Sure then I’m here! | The dialogue is left entirely in English, failing to translate the spoken words into the t | possible untranslated English passage: Sure, it’s an arm, yer honour! | The entire segment remains in the source language (English) and has not been translated in
  - numbers, e.g.: numeric content differs from source: The quantity of distance fallen is 'four thousand mil | numeric content differs from source: In the source, the object of the offense is 'it' (the
- **Book consistency**: 38 findings; [medium] An English closing quotation mark ” is used here, but the bo ×32; [low] The style sheet renders "yer honour" as "Euer Ehren", but th ×4; [low] The style sheet renders "old fellow" as "alter Bursche", but ×2
- **Review queue**: 16 segments
- **Target punctuation** (segments using each form, of 145): {'„ “ quotes': 113, '» « quotes': 0, '” English closing quote': 10, '" straight quotes': 0, '– Gedankenstrich': 20}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | Alice begann, sich sehr zu langweilen, während sie am Ufer neben ihrer Schwester saß und nichts zu tun hatte: Ein- oder zweimal hatte sie in das Buch geschaut,  |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | So überlegte sie in ihrem eigenen Kopf (so gut sie konnte, denn der heiße Tag machte sie sehr schläfrig und dumm), ob der Spaß, eine Gänseblümchenkette zu mache |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | Es war nichts besonders Bemerkenswertes daran; und Alice fand es auch nicht allzu ungewöhnlich, den Hasen bei sich selbst sagen zu hören: „Oh weh! Oh weh! Ich k |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | In einem Augenblick war Alice ihm nachgestürzt, ohne auch nur einmal darüber nachzudenken, wie sie in aller Welt wieder herauskommen sollte. |

## bench-lang-en-ko: English → Korean (en>ko)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 89 LLM calls, 16.3 min; slowest: audit_translation 7.6 min, translate 4.2 min, extract_glossary 1.3 min, repair_translation 1.1 min
- **Glossary**: 36 approved entries, pair en>ko; e.g. Alice → 앨리스; Bill → 빌; Crab → 게; Dinah → 디나; Dodo → 도도새; Duchess → 공작부인; Duck → 오리; Eaglet → 수리새
- **Style sheet characters**: Alice (그녀, —); Bill (그, —); Caterpillar (그, —); Crab (그, —); Dinah (그녀, —); Dodo (그, —); Duck (그, —); Eaglet (그, —)
- **Audit findings** (rules): {'untranslated': 5, 'ai_style': 1}
- **Audit findings** (semantic model): {'mistranslation': 17}
  - untranslated, e.g.: possible untranslated English word: ever | possible untranslated English word: ever | possible untranslated English word: ever | possible untranslated English word: splash | possible untranslated English word: herself
- **Book consistency**: no findings
- **Review queue**: 4 segments
- **Target punctuation** (segments using each form, of 145): {'“ ” quotes': 113, '" straight quotes': 0, '「」 quotes': 0, '…… ellipsis': 0}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | 앨리스는 언덕에 앉아 언니를 바라보며, 할 일이 없다는 사실에 점점 지쳐가고 있었다. 언니가 읽고 있는 책을 몇 번이나 엿보았지만, 그 안에는 그림도 대화도 없었다. “그림도, 대화도 없는 책이 무슨 소용이 있겠어?” 앨리스는 생각했다. |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | 그녀는 머릿속으로 생각에 잠겼다. 더운 날씨 탓에 졸리고 멍한 기분이 들었지만, 가능한 한 최선을 다해 생각했다. 데이지로 꽃줄을 만드는 즐거움이, 일어나서 데이지를 따는 수고에 비해 가치가 있는가. 그때 갑자기 분홍빛 눈을 가진 흰 토끼가 그녀 곁을 스쳐 지나갔다. |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | 그것은 매우 특별할 것이 없었다. 흰 토끼가 자기 자신에게 “아이고! 아이고! 늦겠어!”라고 말하는 소리를 듣는 것도 매우 이상할 일은 아니었다. (사후에 생각해보니, 그때 놀랐어야 했다는 생각이 들었지만, 당시는 모든 것이 너무나 자연스러웠다.) 그러나 흰 토끼가 실제로 베스트 포켓에 |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | 순간, 앨리스는 다시 한번 구멍으로 떨어졌다. 어떻게 다시 나올지에 대해서는 한 번도 생각하지 않았다. |

## bench-lang-es-en: Spanish → English (es>en)

- **Status**: paused; tiers profiled → tuned
- **Skipped checks**: untranslated text, punctuation conventions, character report
- **Cost**: 25 LLM calls, 2.3 min; slowest: repair_review 1.1 min, review_repaired 0.7 min, validate_repaired 0.4 min, approve_glossary 0.0 min
- **Glossary**: 157 approved entries, pair es>en; e.g. Albert → Albert; Alburquerque → Albuquerque; Alejandro Miquis → Alejandro Miquis; Arnaiz → Arnaiz; Ayún → Ayún; Babieca → Babieca; Barbarita → Barbarita; Bastiat → Bastiat
- **Style sheet characters**: Albert (he, —); Arnaiz (he, —); Baldomero (he, —); Barbarita (she, —); Benigna (she, —); Bringas (he, —); Candelaria (she, —); Casarredonda (he, —)
- **Audit findings** (rules): {'glossary': 5, 'mistranslation': 2, 'ai_style': 1}
- **Audit findings** (semantic model): {'mistranslation': 28}
  - numbers, e.g.: numeric content differs from source: SOURCE: 'Comprometido éste del 40 al 45' (referring t | numeric content differs from source: The source specifies the years 'desde el 38 al 60' (f
- **Book consistency**: no findings
- **Review queue**: 2 segments
- **Target punctuation** (segments using each form, of 88): {'“ ” quotes': 27, '" straight quotes': 0}

| source | translation |
|---|---|
| Las noticias más remotas que tengo de la persona que lleva este nombre me las ha dado Jacinto María Villalonga, y alcanzan al tiempo en que este amigo mío y el  | The most remote news I have of the person who bears this name was given to me by Jacinto María Villalonga, and it reaches back to the time when this friend of m |
| ¡Ay!, el susto que se llevaron D. Baldomero Santa Cruz y Barbarita no es para contado. ¡Qué noche de angustia la del 10 al 11! Ambos creían no volver a ver a su | ¡Ay!, the fright that Don Baldomero Santa Cruz and Barbarita suffered is beyond telling. What a night of anguish from the 10th to the 11th! Both believed they w |
| Cuando el niño estudiaba los últimos años de su carrera, verificose en él uno de esos cambiazos críticos que tan comunes son en la edad juvenil. De travieso y a | When the boy was studying the last years of his career, one of those critical changes so common in youth occurred in him. From mischievous and noisy he became s |
| Todos los dineros que su papá le daba, dejábalos Juanito en casa de Bailly-Baillière, a cuenta de los libros que iba tomando. Refiere Villalonga que un día fue  | All the money his father gave him, Juanito left at the house of Bailly-Baillière, on account of the books he was taking. Villalonga relates that one day Barbari |

## bench-lang-es-zh: Spanish → Simplified Chinese (es>zh)

- **Status**: paused; tiers profiled → tuned
- **Skipped checks**: none
- **Cost**: 117 LLM calls, 36.2 min; slowest: audit_translation 18.9 min, translate 6.3 min, extract_glossary 3.8 min, resolve_glossary 2.0 min
- **Glossary**: 135 approved entries, pair es>zh; e.g. Albert → 阿尔贝特; Alejandro Miquis → 亚历杭德罗·米基斯; Aparisi → 阿帕里西; Ayún → 阿云; Barbarita → 巴巴里塔; Bastiat → 巴斯塔; Benigna → 贝尼尼亚; Bonifacio Arnaiz → 博尼法西奥·阿尔奈斯
- **Style sheet characters**: Albert (他, 你); Arnaiz (他, 你); Baldomero (他, 您); Barbarita (她, 你); Bravo Murillo (他, 您); Candelaria (她, 你); Cantero (他, 您); Castita (她, 你)
- **Audit findings** (rules): {'glossary': 12, 'untranslated': 2, 'mistranslation': 2}
- **Audit findings** (semantic model): {'mistranslation': 16, 'omission': 1}
  - untranslated, e.g.: possible untranslated Spanish word: patencures | possible untranslated Spanish word: madapolanes
  - numbers, e.g.: numeric content differs from source: In the source, the linen is described as "lienzos gal | numeric content differs from source: In the SOURCE, the range 'desde el 38 al 60' refers t
- **Book consistency**: no findings
- **Review queue**: 6 segments
- **Target punctuation** (segments using each form, of 88): {'“ ” quotes': 29, '「」 quotes': 0, '" straight quotes': 0, '—— paired dash': 17, '— single dash': 0}

| source | translation |
|---|---|
| Las noticias más remotas que tengo de la persona que lleva este nombre me las ha dado Jacinto María Villalonga, y alcanzan al tiempo en que este amigo mío y el  | 关于这位同名者，我所知最久远的消息来自哈辛托·玛丽亚·维亚隆加，其时间跨度直至这位我的朋友、另一位以及更远处的萨拉梅罗、华金尼托·佩斯、亚历杭德罗·米基斯尚在大学课堂求学的年代。他们并非都在同一年级，尽管在卡穆斯的讲台上相聚，却在罗马法课上分道扬镳：圣克鲁斯家的孩子是诺瓦尔的弟子，而维亚隆加则是科罗纳多的学生。他们的用 |
| ¡Ay!, el susto que se llevaron D. Baldomero Santa Cruz y Barbarita no es para contado. ¡Qué noche de angustia la del 10 al 11! Ambos creían no volver a ver a su | 哎呀，多尼奥·巴尔多梅罗·圣克鲁斯和巴巴里塔受到的惊吓，简直无法言表。从10日到11日的那夜，焦虑何等深重！两人都以为再也见不到他们心爱的孩子了。作为独子，孩子承载了他们无尽的父母之爱，尽管他们并不年迈。当那个叫胡安尼托的孩子走进家门，面色苍白，饥肠辘辘，那张可爱的脸容憔悴，衣服上沾满污渍，散发着乡野气息时，他的母亲在 |
| Cuando el niño estudiaba los últimos años de su carrera, verificose en él uno de esos cambiazos críticos que tan comunes son en la edad juvenil. De travieso y a | 当孩子攻读学位的最后几年时，他经历了一次青年时期常见的剧烈转变。从顽皮好动变得极其审慎，甚至让萨拉梅罗都自叹不如。他产生了严格履行学业义务的热忱，甚至通过无限制的阅读和与朋友间的辩论及雄辩练习来自我提升。他不仅准时上课，笔记详尽，还坐在第一排，以专注的神情凝视教授，目不转睛，仿佛凝视恋人，并以点头表示赞同，仿佛在说：“ |
| Todos los dineros que su papá le daba, dejábalos Juanito en casa de Bailly-Baillière, a cuenta de los libros que iba tomando. Refiere Villalonga que un día fue  | 他父亲给他的所有钱，胡安尼托都留在贝利-巴利耶书店，作为他陆续取书的预付款。维亚隆加讲述，有一天巴巴里塔欣喜若狂，充满自豪地去了书店，在结清孩子的债务后，吩咐书店交付他要求的所有书籍，无论多贵或多大如弥撒书。这位善良如天使的女士想为她的母性虚荣心加上一道谦逊的缰绳。她觉得在已出生和未出生的所有孩子中显示儿子的优越，会冒 |


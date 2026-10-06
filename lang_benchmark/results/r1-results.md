# Language-profile benchmarks

## bench-r1-qwen-en-zh: English → Simplified Chinese (en-zh)

- **Status**: paused; tiers tuned → tuned
- **Skipped checks**: none
- **Cost**: 48 LLM calls, 11.1 min; slowest: audit_translation 4.6 min, translate 2.7 min, extract_glossary 0.9 min, reprose_translation 0.9 min
- **Glossary**: 37 approved entries, pair en-zh; e.g. Ada → 阿达; Alice → 爱丽丝; Cheshire Cat → 柴郡猫; Dinah → 迪娜; Duchess → 公爵夫人; Gryphon → 狮鹫; King → 国王; King of Hearts → 红心国王
- **Audit findings** (rules): {}
- **Audit findings** (semantic model): {'mistranslation': 6, 'omission': 1}
- **Book consistency**: 2 findings; [medium] Repeated quoted line "Off with his head" is rendered "砍掉它的头！ ×1; [low] Repeated quoted line "Come on, then" is rendered "走吧，" here  ×1
- **Review queue**: 1 segments
- **Target punctuation** (segments using each form, of 191): {'“ ” quotes': 165, '「」 quotes': 0, '" straight quotes': 1, '—— paired dash': 32, '— single dash': 0}

| source | translation |
|---|---|
| “Curiouser and curiouser!” cried Alice (she was so much surprised, that for the moment she quite forgot how to speak good English); “now I’m opening out like th | “越发奇怪，越发奇怪！”爱丽丝喊道（她惊讶得一时竟忘了该怎么说地道的英语）；“现在我像有史以来最大的望远镜一样伸展开了！再见啦，小脚丫！”（因为她低头看脚时，它们似乎已快消失在视野之外，离得太远了）。“哦，我可怜的小脚丫，我真不知道现在谁来给你们穿鞋袜呢，亲爱的？我肯定我做不到！我离得太远了，顾不上你们：你们得自己想办 |
| And she went on planning to herself how she would manage it. “They must go by the carrier,” she thought; “and how funny it’ll seem, sending presents to one’s ow | 她继续在心里盘算着该怎么办。“得用邮车送，”她想；“给自己脚寄礼物，那该多滑稽！地址写起来该多古怪！" |
| Alice’s Right Foot, Esq., Hearthrug, near the Fender, (with Alice’s love). | 爱丽丝的右脚，阁下，炉毯旁，靠近炉栅，（附爱丽丝的爱）。 |
| Just then her head struck against the roof of the hall: in fact she was now more than nine feet high, and she at once took up the little golden key and hurried  | 就在这时，她的头撞到了大厅的天花板：事实上，她现在已长到九英尺多高，她立刻拿起那把小金钥匙，匆匆奔向花园的门。 |

## bench-r1-tg-en-ko: English → Korean (en>ko)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 263 LLM calls, 109.3 min; slowest: audit_translation 84.0 min, translate 15.0 min, repair_translation 5.8 min, extract_glossary 1.4 min
- **Glossary**: 43 approved entries, pair en>ko; e.g. Ada → 아다; Alice → 앨리스; Bill → 빌; caterpillar → 나방; Crab → 게; Dinah → 디나; Dodo → 도도새; Duchess → 공작부인
- **Style sheet characters**: Alice (그녀, —); Bill (그, —); caterpillar (그, —); Crab (그, —); Dodo (그, —); Duck (그, —); Eaglet (그, —); Lory (그, —)
- **Audit findings** (rules): {'glossary': 3, 'mistranslation': 3, 'untranslated': 2}
- **Audit findings** (semantic model): {'mistranslation': 42, 'omission': 11, 'untranslated': 3}
  - untranslated, e.g.: A massive section of the translation is an unsupported addition. The text from '나는 여기 아래에  | The translation fails to render the source fragment 'How doth the little—' and instead rep | text in a script neither language uses: 溺死 | The translation introduces the name '앨리스' (Alice), which does not appear in the source seg | text in a script neither language uses: 着手
  - numbers, e.g.: numeric content differs from source: The source quantity 'half an hour or so' (approximate | numeric content differs from source: The polarity of the quantity-bearing sentiment in the | numeric content differs from source: SOURCE: 'about a thousand times as large as the Rabbi
- **Book consistency**: 7 findings; [low] The style sheet renders "yer honour" as "명령하소서", but this tr ×3; [low] The style sheet renders "I beg your pardon!" as "실례를 용서해 주십시 ×2; [low] The style sheet renders "Oh dear!" as "아이고!", but this trans ×1; [low] Repeated quoted line "I beg your pardon" is rendered "죄송합니다. ×1
- **Review queue**: 2 segments
- **Target punctuation** (segments using each form, of 145): {'“ ” quotes': 113, '" straight quotes': 0, '「」 quotes': 0, '…… ellipsis': 0}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | 앨리스는 강둑에 앉아 있는 여동생 옆에 앉아 있는 것이 점점 지루해지고, 할 일이 없어지자, 여동생이 읽고 있는 책을 흘끗 보았지만, 그림이나 대화가 없어서 “그림이나 대화가 없는 책은 무슨 소용일까?”라고 생각했다. |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | 그래서 앨리스는 마음속으로 생각했다(더운 날씨 때문에 앨리스는 매우 졸리고 어리석게 느껴졌지만), 데이지 꽃으로 목걸이를 만드는 즐거움이 데이지를 꺾으러 일어나서 하는 수고로움만큼 가치가 있을지. 그러자 갑자기 분홍색 눈을 가진 흰 토끼가 그녀 옆을 빠르게 지나갔다. |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | 그것에 특별히 눈에 띄는 점은 없었고, 앨리스도 토끼가 “아이고! 아이고! 늦겠어!”라고 혼잣말하는 것이 그렇게 이상하다고 생각하지 않았다(나중에 다시 생각해보니, 그 점에 대해 의아해했어야 한다고 생각했지만, 그때는 모두 자연스럽게 느껴졌다). 하지만 토끼가 실제로 조끼 주머니에서 시 |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | 잠시 후 앨리스도 토끼를 따라 굴 속으로 뛰어들었고, 어떻게 다시 밖으로 나올 수 있을지 전혀 생각하지 않았다. |

## bench-r1-tg-zh-ja: Simplified Chinese → Japanese (zh>ja)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: untranslated text, character report, prose rewrite
- **Cost**: 262 LLM calls, 73.6 min; slowest: audit_translation 39.4 min, translate 19.9 min, repair_translation 8.0 min, extract_glossary 3.0 min
- **Glossary**: 151 approved entries, pair zh>ja; e.g. 吴妈 → 呉媽; 妲己 → 妲己; 小D → 小D; 小Don → 小Don; 小尼姑 → 小尼姑; 少奶奶 → 少奶奶; 王癞胡 → 王癩胡; 王胡 → 王胡
- **Style sheet characters**: 假洋鬼子 (彼, —); 吴妈 (彼女, —); 地保 (彼, —); 小D (彼, —); 小尼姑 (彼女, —); 少奶奶 (彼女, 様); 王胡 (彼, —); 秀才 (彼, —)
- **Audit findings** (rules): {'glossary': 80, 'addition': 9, 'mistranslation': 5, 'duplication': 3, 'omission': 2}
- **Audit findings** (semantic model): {'mistranslation': 35, 'untranslated': 3, 'omission': 2, 'glossary': 2}
  - untranslated, e.g.: The translation omits the first part of the source fragment regarding the monk. | The translation fails to render any part of the source text. | The final phrase of the sentence is left untranslated in the source language (Chinese), wh
  - numbers, e.g.: numeric content differs from source: In the source, the length of the onion leaf is '半寸长' 
- **Book consistency**: no findings
- **Review queue**: 1 segments
- **Target punctuation** (segments using each form, of 163): {'「」 quotes': 77, '" or “ ” quotes': 0, '…… ellipsis': 32, '… or ... ellipsis': 0}

| source | translation |
|---|---|
| 我要给阿Q做正传，已经不止一两年了。但一面要做，一面又往回想，这足见我不是一个『立言』的人，因为从来不朽之笔，须传不朽之人，于是人以文传，文以人传——究竟谁靠谁传，渐渐的不甚了然起来，而终于归结到传阿Q，仿佛思想里有鬼似的。 | 私は阿Qの伝記を書こうと、もう一年以上も前から考えてきた。しかし、書き始めようとすると、また考え直してしまう。これは、私が「立言」をする人間ではないことを示している。なぜなら、不朽の筆は、不朽の人を伝えるべきであり、人は文章によって伝えられ、文章は人によって伝えられるからだ。結局、誰が誰を伝えるのか、だんだんわからなく |
| 然而要做这一篇速朽的文章，才下笔，便感到万分的困难了。第一是文章的名目。孔子曰：『名不正则言不顺。』这原是应该极注意的。传的名目很繁多：列传、自传、内传、外传、别传、家传、小传，……而可惜都不合。『列传』么，这一篇并非和许多阔人排在『正史』里；『自传』么，我又并非就是阿Q。说是『外传』，『内传』在那里呢？倘用『内传』， | しかし、この速朽するような文章を書こうとすると、すぐに多くの困難に直面する。まず、文章の題名だ。孔子は「名不正則言不順」と言った。これは、非常に注意すべきことだ。伝記には、列伝、自伝、内伝、外伝、別伝、家伝、小伝など、さまざまな種類がある。しかし、残念ながら、どれも阿Qには当てはまらない。『列伝』は、多くの著名人と並ん |
| 第二，立传的通例，开首大抵该是『某，字某，某地人也』，而我并不知道阿Q姓什么。有一回，他似乎是姓赵，但第二日便模糊了。那是赵太爷的儿子进了秀才的时候，锣声铛铛的报到村里来，阿Q正喝了两碗黄酒，便手舞足蹈的说，这于他也很光采，因为他和赵太爷原来是本家，细细的排起来他还比秀才长三辈呢。其时几个旁听人倒也肃然的有些起敬了。那 | 第二に、伝記を書く際の一般的な形式は、冒頭に「〇〇、字〇〇、〇〇の出身」と書くことだ。しかし、私は阿Qの姓が何であるかわからない。ある時、彼は趙姓のようだったが、次の日には曖昧になってしまった。それは、趙太爺の息子が秀才になった時、村に太鼓の音が響き渡り、阿Qは酒を二杯飲んで、手足を振り回しながら言った。「これは、私に |
| 阿Q并没有抗辩他确凿姓赵，只用手摸着左颊，和地保退出去了；外面又被地保训斥了一番，谢了地保二百文酒钱。知道的人都说阿Q太荒唐，自己去招打；他大约未必姓赵，即使真姓赵，有赵太爷在这里，也不该如此胡说的。此后便再没有人提起他的氏族来，所以我终于不知道阿Q究竟什么姓。 | 阿Qは、自分が本当に趙姓であることを否定しなかった。ただ、左の頬をさすりながら、地保と一緒にそこから出て行った。外に出ると、地保からさらに叱責を受け、二百文の酒代を渡された。人々は、阿Qがあまりにも無分別で、自分から殴られるようにしたのだと言った。彼は、もしかしたら趙姓ではないかもしれない。もし本当に趙姓だとしても、趙 |


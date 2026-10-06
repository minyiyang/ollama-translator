# Language-profile benchmarks

## bench-r2-tg-en-ko: English → Korean (en>ko)

- **Status**: paused; tiers tuned → profiled
- **Skipped checks**: character report, prose rewrite
- **Cost**: 262 LLM calls, 91.7 min; slowest: audit_translation 66.7 min, translate 15.1 min, repair_translation 4.1 min, repair_review 2.2 min
- **Glossary**: 43 approved entries, pair en>ko; e.g. Ada → 아다; Alice → 앨리스; Bill → 빌; caterpillar → 나방; Crab → 게; Dinah → 디나; Dodo → 도도새; Duchess → 공작부인
- **Style sheet characters**: Alice (그녀, —); Bill (그, —); caterpillar (그, —); Crab (그, —); Dodo (그, —); Duck (그, —); Eaglet (그, —); Lory (그, —)
- **Audit findings** (rules): {'glossary': 4, 'mistranslation': 3, 'untranslated': 2}
- **Audit findings** (semantic model): {'mistranslation': 40, 'omission': 9, 'untranslated': 1, 'structure': 1, 'glossary': 1}
  - untranslated, e.g.: text in a script neither language uses: 出路 | The translation introduces the name '앨리스' (Alice), which does not appear in the source seg | text in a script neither language uses: 着手
  - numbers, e.g.: numeric content differs from source: The source quantity 'half an hour or so' (approximate | numeric content differs from source: The polarity of the quantity-bearing sentiment in the | numeric content differs from source: SOURCE: 'about a thousand times as large as the Rabbi
- **Book consistency**: 7 findings; [low] The style sheet renders "yer honour" as "명령하소서", but this tr ×3; [low] The style sheet renders "I beg your pardon!" as "실례를 용서해 주십시 ×2; [low] The style sheet renders "Oh dear!" as "아이고!", but this trans ×1; [low] Repeated quoted line "I beg your pardon" is rendered "죄송합니다. ×1
- **Review queue**: 2 segments
- **Target punctuation** (segments using each form, of 145): {'“ ” quotes': 114, '" straight quotes': 0, '「」 quotes': 0, '…… ellipsis': 0}

| source | translation |
|---|---|
| Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: once or twice she had peeped into the book her sister w | 앨리스는 강둑에 앉아 있는 여동생 옆에 앉아 있는 것이 점점 지루해지고, 할 일이 없어지자, 여동생이 읽고 있는 책을 흘끗 보았지만, 그림이나 대화가 없어서 “그림이나 대화가 없는 책은 무슨 소용일까?”라고 생각했다. |
| So she was considering in her own mind (as well as she could, for the hot day made her feel very sleepy and stupid), whether the pleasure of making a daisy-chai | 그래서 앨리스는 마음속으로 생각했다(더운 날씨 때문에 앨리스는 매우 졸리고 어리석게 느껴졌지만), 데이지 꽃으로 목걸이를 만드는 즐거움이 데이지를 꺾으러 일어나서 하는 수고로움만큼 가치가 있을지. 그러자 갑자기 분홍색 눈을 가진 흰 토끼가 그녀 옆을 빠르게 지나갔다. |
| There was nothing so very remarkable in that; nor did Alice think it so very much out of the way to hear the Rabbit say to itself, “Oh dear! Oh dear! I shall be | 그것에 특별히 눈에 띄는 점은 없었고, 앨리스도 토끼가 “아이고! 아이고! 늦겠어!”라고 혼잣말하는 것이 그렇게 이상하다고 생각하지 않았다(나중에 다시 생각해보니, 그 점에 대해 의아해했어야 한다고 생각했지만, 그때는 모두 자연스럽게 느껴졌다). 하지만 토끼가 실제로 조끼 주머니에서 시 |
| In another moment down went Alice after it, never once considering how in the world she was to get out again. | 잠시 후 앨리스도 토끼를 따라 굴 속으로 뛰어들었고, 어떻게 다시 밖으로 나올 수 있을지 전혀 생각하지 않았다. |


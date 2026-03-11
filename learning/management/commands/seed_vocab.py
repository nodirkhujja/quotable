"""
python manage.py seed_vocab

Seeds the VocabWord table with 100 curated English words across 5 tiers.
Each tier represents a 200-word frequency band (1-200, 201-400, …, 801-1000).
Safe to run multiple times — uses update_or_create on the word field.
"""

from django.core.management.base import BaseCommand

from learning.models import VocabWord

# fmt: off
WORDS = [
    # ── Tier 1 — Core (rank 1-200, most common meaningful words) ──────────
    dict(word="make",    frequency_rank=10,  tier=1, pos="v",   uzbek_translation="qilmoq",      uzbek_synonyms="yasamoq, bajarmoq",    example_sentence="Can you make dinner tonight?"),
    dict(word="get",     frequency_rank=15,  tier=1, pos="v",   uzbek_translation="olmoq",        uzbek_synonyms="qo'lga kiritmoq",      example_sentence="I need to get some rest."),
    dict(word="go",      frequency_rank=20,  tier=1, pos="v",   uzbek_translation="bormoq",       uzbek_synonyms="ketmoq, yo'lga tushmoq",example_sentence="Let's go to the store."),
    dict(word="know",    frequency_rank=25,  tier=1, pos="v",   uzbek_translation="bilmoq",       uzbek_synonyms="anglash, tushunmoq",   example_sentence="Do you know her name?"),
    dict(word="time",    frequency_rank=30,  tier=1, pos="n",   uzbek_translation="vaqt",         uzbek_synonyms="muddat, fursat",        example_sentence="There's no time to waste."),
    dict(word="good",    frequency_rank=35,  tier=1, pos="adj", uzbek_translation="yaxshi",       uzbek_synonyms="a'lo, zo'r",           example_sentence="That's a really good idea."),
    dict(word="new",     frequency_rank=40,  tier=1, pos="adj", uzbek_translation="yangi",        uzbek_synonyms="fresh, zamonaviy",     example_sentence="She bought a new phone."),
    dict(word="think",   frequency_rank=45,  tier=1, pos="v",   uzbek_translation="o'ylamoq",     uzbek_synonyms="fikr yuritmoq",        example_sentence="I think you're right."),
    dict(word="say",     frequency_rank=50,  tier=1, pos="v",   uzbek_translation="aytmoq",       uzbek_synonyms="gapirmoq, so'zlamoq",  example_sentence="What did she say?"),
    dict(word="want",    frequency_rank=55,  tier=1, pos="v",   uzbek_translation="xohlamoq",     uzbek_synonyms="istamoq, talab qilmoq",example_sentence="I want to learn English."),
    dict(word="look",    frequency_rank=60,  tier=1, pos="v",   uzbek_translation="qaralamoq",    uzbek_synonyms="ko'rmoq, kuzatmoq",    example_sentence="Look at that sunset!"),
    dict(word="come",    frequency_rank=65,  tier=1, pos="v",   uzbek_translation="kelmoq",       uzbek_synonyms="yetib kelmoq",         example_sentence="Come here, please."),
    dict(word="give",    frequency_rank=70,  tier=1, pos="v",   uzbek_translation="bermoq",       uzbek_synonyms="topshirmoq, hadya etmoq",example_sentence="Give me a moment."),
    dict(word="find",    frequency_rank=80,  tier=1, pos="v",   uzbek_translation="topmoq",       uzbek_synonyms="kashf etmoq",          example_sentence="Can you find the keys?"),
    dict(word="day",     frequency_rank=90,  tier=1, pos="n",   uzbek_translation="kun",          uzbek_synonyms="sana, kunduz",         example_sentence="Have a great day!"),
    dict(word="way",     frequency_rank=100, tier=1, pos="n",   uzbek_translation="yo'l",         uzbek_synonyms="usul, metod",          example_sentence="Is there another way?"),
    dict(word="back",    frequency_rank=110, tier=1, pos="adv", uzbek_translation="orqaga",       uzbek_synonyms="qayta, yana",          example_sentence="I'll be back in an hour."),
    dict(word="work",    frequency_rank=120, tier=1, pos="v",   uzbek_translation="ishlamoq",     uzbek_synonyms="mehnat qilmoq",        example_sentence="She works very hard."),
    dict(word="first",   frequency_rank=130, tier=1, pos="adj", uzbek_translation="birinchi",     uzbek_synonyms="boshlang'ich, dastlabki",example_sentence="This is my first time here."),
    dict(word="use",     frequency_rank=140, tier=1, pos="v",   uzbek_translation="ishlatmoq",    uzbek_synonyms="qo'llamoq, foydalanmoq",example_sentence="Use a dictionary to find it."),

    # ── Tier 2 — Common (rank 201-400) ────────────────────────────────────
    dict(word="place",   frequency_rank=210, tier=2, pos="n",   uzbek_translation="joy",          uzbek_synonyms="manzil, hudud",        example_sentence="This is a beautiful place."),
    dict(word="show",    frequency_rank=220, tier=2, pos="v",   uzbek_translation="ko'rsatmoq",   uzbek_synonyms="namoyish etmoq",       example_sentence="Show me what you found."),
    dict(word="keep",    frequency_rank=230, tier=2, pos="v",   uzbek_translation="saqlash",      uzbek_synonyms="saqlamoq, ushlab turmoq",example_sentence="Keep going, you're doing great."),
    dict(word="play",    frequency_rank=240, tier=2, pos="v",   uzbek_translation="o'ynamoq",     uzbek_synonyms="ijro etmoq",           example_sentence="The kids love to play outside."),
    dict(word="move",    frequency_rank=250, tier=2, pos="v",   uzbek_translation="harakatlanmoq",uzbek_synonyms="ko'chirmoq, siljitmoq",example_sentence="It's time to move forward."),
    dict(word="live",    frequency_rank=260, tier=2, pos="v",   uzbek_translation="yashmoq",      uzbek_synonyms="istiqomat qilmoq",     example_sentence="Where do you live?"),
    dict(word="point",   frequency_rank=270, tier=2, pos="n",   uzbek_translation="nuqta",        uzbek_synonyms="maqsad, jihat",        example_sentence="What's the point of this?"),
    dict(word="turn",    frequency_rank=280, tier=2, pos="v",   uzbek_translation="burilmoq",     uzbek_synonyms="o'girmoq, aylantirmoq",example_sentence="Turn left at the corner."),
    dict(word="start",   frequency_rank=290, tier=2, pos="v",   uzbek_translation="boshlash",     uzbek_synonyms="boshlamoq",            example_sentence="Let's start from the beginning."),
    dict(word="hand",    frequency_rank=300, tier=2, pos="n",   uzbek_translation="qo'l",         uzbek_synonyms="kaft, barmoqlar",      example_sentence="Give me your hand."),
    dict(word="hard",    frequency_rank=310, tier=2, pos="adj", uzbek_translation="qiyin",        uzbek_synonyms="qattiq, og'ir",        example_sentence="This is a hard question."),
    dict(word="small",   frequency_rank=320, tier=2, pos="adj", uzbek_translation="kichik",       uzbek_synonyms="kichkina, oz",         example_sentence="She lives in a small apartment."),
    dict(word="large",   frequency_rank=330, tier=2, pos="adj", uzbek_translation="katta",        uzbek_synonyms="ulkan, keng",          example_sentence="They need a large team."),
    dict(word="number",  frequency_rank=340, tier=2, pos="n",   uzbek_translation="raqam",        uzbek_synonyms="son, miqdor",          example_sentence="What's your phone number?"),
    dict(word="happen",  frequency_rank=350, tier=2, pos="v",   uzbek_translation="sodir bo'lmoq",uzbek_synonyms="yuz bermoq, ro'y bermoq",example_sentence="What happened last night?"),
    dict(word="carry",   frequency_rank=360, tier=2, pos="v",   uzbek_translation="ko'tarmoq",    uzbek_synonyms="olib yurmoq, tashimoq",example_sentence="Can you carry this bag?"),
    dict(word="might",   frequency_rank=370, tier=2, pos="v",   uzbek_translation="bo'lishi mumkin",uzbek_synonyms="ehtimol",             example_sentence="It might rain tomorrow."),
    dict(word="feel",    frequency_rank=380, tier=2, pos="v",   uzbek_translation="his etmoq",    uzbek_synonyms="sezmoq, tuymoq",       example_sentence="I feel much better now."),
    dict(word="mind",    frequency_rank=390, tier=2, pos="n",   uzbek_translation="aql",          uzbek_synonyms="ong, fikr",            example_sentence="Keep it in mind."),
    dict(word="try",     frequency_rank=400, tier=2, pos="v",   uzbek_translation="urinib ko'rmoq",uzbek_synonyms="harakat qilmoq",      example_sentence="Just try your best."),

    # ── Tier 3 — Familiar (rank 401-600) ──────────────────────────────────
    dict(word="allow",   frequency_rank=410, tier=3, pos="v",   uzbek_translation="ruxsat bermoq",uzbek_synonyms="ijozat bermoq",        example_sentence="They don't allow phones here."),
    dict(word="provide", frequency_rank=430, tier=3, pos="v",   uzbek_translation="ta'minlamoq",  uzbek_synonyms="bermoq, yetkazmoq",    example_sentence="Can you provide more details?"),
    dict(word="develop", frequency_rank=440, tier=3, pos="v",   uzbek_translation="rivojlanmoq",  uzbek_synonyms="taraqqiy etmoq",       example_sentence="We need to develop new skills."),
    dict(word="result",  frequency_rank=450, tier=3, pos="n",   uzbek_translation="natija",       uzbek_synonyms="oqibat, chiqit",       example_sentence="The results were surprising."),
    dict(word="issue",   frequency_rank=460, tier=3, pos="n",   uzbek_translation="masala",       uzbek_synonyms="muammo, savol",        example_sentence="That's not really the issue."),
    dict(word="support", frequency_rank=470, tier=3, pos="v",   uzbek_translation="qo'llamoq",    uzbek_synonyms="yordam bermoq",        example_sentence="I support your decision."),
    dict(word="include", frequency_rank=480, tier=3, pos="v",   uzbek_translation="qamrab olmoq", uzbek_synonyms="kiritmoq",             example_sentence="Please include your name."),
    dict(word="change",  frequency_rank=490, tier=3, pos="v",   uzbek_translation="o'zgartirmoq", uzbek_synonyms="almashmoq",            example_sentence="Everything can change."),
    dict(word="explain", frequency_rank=500, tier=3, pos="v",   uzbek_translation="tushuntirmoq", uzbek_synonyms="izohlash",             example_sentence="Can you explain that again?"),
    dict(word="major",   frequency_rank=510, tier=3, pos="adj", uzbek_translation="asosiy",       uzbek_synonyms="muhim, katta",         example_sentence="This is a major problem."),
    dict(word="process", frequency_rank=520, tier=3, pos="n",   uzbek_translation="jarayon",      uzbek_synonyms="tartib, bosqich",      example_sentence="Trust the process."),
    dict(word="report",  frequency_rank=530, tier=3, pos="n",   uzbek_translation="hisobot",      uzbek_synonyms="xabar, ma'lumot",      example_sentence="Write a report about it."),
    dict(word="fact",    frequency_rank=540, tier=3, pos="n",   uzbek_translation="haqiqat",      uzbek_synonyms="fakt, voqelik",        example_sentence="In fact, it happened yesterday."),
    dict(word="entire",  frequency_rank=550, tier=3, pos="adj", uzbek_translation="butun",        uzbek_synonyms="to'liq, hammasi",      example_sentence="I read the entire book."),
    dict(word="national",frequency_rank=560, tier=3, pos="adj", uzbek_translation="milliy",       uzbek_synonyms="davlatga oid",         example_sentence="It's a national holiday."),
    dict(word="local",   frequency_rank=570, tier=3, pos="adj", uzbek_translation="mahalliy",     uzbek_synonyms="hududiy, joydagi",     example_sentence="Buy from local businesses."),
    dict(word="obvious", frequency_rank=575, tier=3, pos="adj", uzbek_translation="aniq",         uzbek_synonyms="ravshan, ochiq",       example_sentence="It's obvious she's upset."),
    dict(word="manage",  frequency_rank=580, tier=3, pos="v",   uzbek_translation="boshqarmoq",   uzbek_synonyms="idora qilmoq",         example_sentence="She manages a big team."),
    dict(word="recent",  frequency_rank=590, tier=3, pos="adj", uzbek_translation="yaqinda",      uzbek_synonyms="so'nggi, yangi",       example_sentence="In recent years things changed."),
    dict(word="reduce",  frequency_rank=600, tier=3, pos="v",   uzbek_translation="kamaytirmoq",  uzbek_synonyms="qisqartirmoq",         example_sentence="We need to reduce costs."),

    # ── Tier 4 — Advanced (rank 601-800) ──────────────────────────────────
    dict(word="achieve",      frequency_rank=610, tier=4, pos="v",   uzbek_translation="erishmoq",       uzbek_synonyms="amalga oshirmoq",      example_sentence="You can achieve anything."),
    dict(word="acknowledge",  frequency_rank=620, tier=4, pos="v",   uzbek_translation="tan olmoq",      uzbek_synonyms="e'tirof etmoq",        example_sentence="He acknowledged his mistake."),
    dict(word="considerable", frequency_rank=630, tier=4, pos="adj", uzbek_translation="sezilarli",      uzbek_synonyms="muhim, katta",         example_sentence="There's a considerable risk."),
    dict(word="demonstrate",  frequency_rank=640, tier=4, pos="v",   uzbek_translation="ko'rsatmoq",     uzbek_synonyms="isbotlamoq, namoyish",  example_sentence="Demonstrate how it works."),
    dict(word="emerge",       frequency_rank=650, tier=4, pos="v",   uzbek_translation="paydo bo'lmoq",  uzbek_synonyms="chiqmoq, ko'rinmoq",   example_sentence="A new leader will emerge."),
    dict(word="fundamental",  frequency_rank=660, tier=4, pos="adj", uzbek_translation="asosiy",         uzbek_synonyms="fundamental, asosli",  example_sentence="Trust is fundamental."),
    dict(word="generate",     frequency_rank=670, tier=4, pos="v",   uzbek_translation="yaratmoq",       uzbek_synonyms="hosil qilmoq",         example_sentence="This will generate income."),
    dict(word="inevitable",   frequency_rank=680, tier=4, pos="adj", uzbek_translation="muqarrar",       uzbek_synonyms="oldini olib bo'lmaydigan",example_sentence="Change is inevitable."),
    dict(word="interpret",    frequency_rank=690, tier=4, pos="v",   uzbek_translation="talqin qilmoq",  uzbek_synonyms="izohlash, tushunmoq",  example_sentence="How do you interpret this?"),
    dict(word="maintain",     frequency_rank=700, tier=4, pos="v",   uzbek_translation="saqlamoq",       uzbek_synonyms="ta'minlamoq, ushlab turmoq",example_sentence="Maintain eye contact."),
    dict(word="perceive",     frequency_rank=710, tier=4, pos="v",   uzbek_translation="idrok etmoq",    uzbek_synonyms="sezmoq, anglash",       example_sentence="How we perceive the world matters."),
    dict(word="sufficient",   frequency_rank=720, tier=4, pos="adj", uzbek_translation="yetarli",        uzbek_synonyms="kifoya, to'liq",        example_sentence="Is that sufficient proof?"),
    dict(word="contemporary", frequency_rank=730, tier=4, pos="adj", uzbek_translation="zamonaviy",      uzbek_synonyms="hozirgi, bugungi",      example_sentence="Contemporary art is interesting."),
    dict(word="elaborate",    frequency_rank=740, tier=4, pos="v",   uzbek_translation="batafsil aytmoq",uzbek_synonyms="tushuntirmoq",          example_sentence="Could you elaborate on that?"),
    dict(word="apparent",     frequency_rank=750, tier=4, pos="adj", uzbek_translation="ko'rinishdan",   uzbek_synonyms="aniq, ravshan",         example_sentence="The answer was apparent."),
    dict(word="complex",      frequency_rank=760, tier=4, pos="adj", uzbek_translation="murakkab",       uzbek_synonyms="qiyin, ko'p qirrali",   example_sentence="It's a complex situation."),
    dict(word="circumstance", frequency_rank=770, tier=4, pos="n",   uzbek_translation="sharoit",        uzbek_synonyms="holat, vaziyat",         example_sentence="Under these circumstances…"),
    dict(word="consequently", frequency_rank=780, tier=4, pos="adv", uzbek_translation="natijada",       uzbek_synonyms="shuning uchun",         example_sentence="Consequently, we had to leave."),
    dict(word="determined",   frequency_rank=790, tier=4, pos="adj", uzbek_translation="qat'iy",         uzbek_synonyms="irodali, maqsadli",     example_sentence="She is very determined."),
    dict(word="significant",  frequency_rank=800, tier=4, pos="adj", uzbek_translation="muhim",          uzbek_synonyms="sezilarli, katta",      example_sentence="A significant change occurred."),

    # ── Tier 5 — Expert (rank 801-1000) ───────────────────────────────────
    dict(word="ambiguous",    frequency_rank=810, tier=5, pos="adj", uzbek_translation="noaniq",          uzbek_synonyms="ikki ma'noli, tushunarsiz",example_sentence="The instructions were ambiguous."),
    dict(word="analogous",    frequency_rank=820, tier=5, pos="adj", uzbek_translation="o'xshash",        uzbek_synonyms="muqobil, analogik",     example_sentence="This is analogous to that case."),
    dict(word="arbitrary",    frequency_rank=830, tier=5, pos="adj", uzbek_translation="o'zboshimchalik", uzbek_synonyms="ixtiyoriy, asossiz",    example_sentence="The choice felt arbitrary."),
    dict(word="connotation",  frequency_rank=840, tier=5, pos="n",   uzbek_translation="yashirin ma'no",  uzbek_synonyms="nozik ma'no",           example_sentence="That word has a negative connotation."),
    dict(word="discrepancy",  frequency_rank=850, tier=5, pos="n",   uzbek_translation="tafovut",         uzbek_synonyms="nomuvofiqlik, farq",    example_sentence="There's a discrepancy in the data."),
    dict(word="elusive",      frequency_rank=860, tier=5, pos="adj", uzbek_translation="qochib yuradigan",uzbek_synonyms="ushlab bo'lmaydigan",   example_sentence="Peace can be elusive."),
    dict(word="exacerbate",   frequency_rank=870, tier=5, pos="v",   uzbek_translation="yomonlashtirmoq", uzbek_synonyms="kuchaytirmoq",          example_sentence="Stress can exacerbate illness."),
    dict(word="facilitate",   frequency_rank=880, tier=5, pos="v",   uzbek_translation="osonlashtirmoq",  uzbek_synonyms="yordam bermoq",         example_sentence="Technology facilitates learning."),
    dict(word="hypothetical", frequency_rank=890, tier=5, pos="adj", uzbek_translation="taxminiy",        uzbek_synonyms="gipotetetik, shartli",  example_sentence="It's a hypothetical scenario."),
    dict(word="inherent",     frequency_rank=895, tier=5, pos="adj", uzbek_translation="tug'ma",          uzbek_synonyms="ichki, azaliy",         example_sentence="There's an inherent risk here."),
    dict(word="meticulous",   frequency_rank=900, tier=5, pos="adj", uzbek_translation="puxta",           uzbek_synonyms="sinchkov, e'tiborli",   example_sentence="She is meticulous about details."),
    dict(word="nuance",       frequency_rank=910, tier=5, pos="n",   uzbek_translation="nozik farq",      uzbek_synonyms="inchalik, soyabop",     example_sentence="There's a nuance you missed."),
    dict(word="ostensibly",   frequency_rank=920, tier=5, pos="adv", uzbek_translation="ko'rinishda",     uzbek_synonyms="taxminan, go'yo",       example_sentence="He was ostensibly calm."),
    dict(word="paradox",      frequency_rank=930, tier=5, pos="n",   uzbek_translation="paradoks",        uzbek_synonyms="ziddiyat, qarama-qarshilik",example_sentence="It's a paradox of success."),
    dict(word="pragmatic",    frequency_rank=940, tier=5, pos="adj", uzbek_translation="amaliy",          uzbek_synonyms="pratikal, real",         example_sentence="Take a pragmatic approach."),
    dict(word="proficient",   frequency_rank=950, tier=5, pos="adj", uzbek_translation="malakali",        uzbek_synonyms="mohir, tajribali",      example_sentence="She is proficient in French."),
    dict(word="rhetoric",     frequency_rank=960, tier=5, pos="n",   uzbek_translation="notiqlik",        uzbek_synonyms="ritorika, so'zama-so'zlik",example_sentence="That's just political rhetoric."),
    dict(word="scrutinize",   frequency_rank=970, tier=5, pos="v",   uzbek_translation="sinchiklab tekshirmoq",uzbek_synonyms="ko'zdan kechirmoq",example_sentence="Scrutinize every detail."),
    dict(word="ubiquitous",   frequency_rank=980, tier=5, pos="adj", uzbek_translation="hamma joyda bor", uzbek_synonyms="keng tarqalgan",        example_sentence="Smartphones are ubiquitous."),
    dict(word="verbose",      frequency_rank=990, tier=5, pos="adj", uzbek_translation="ko'p so'zli",     uzbek_synonyms="uzoq, ortiqcha so'zli", example_sentence="His explanation was too verbose."),
]
# fmt: on


class Command(BaseCommand):
    help = "Seed the VocabWord table with 100 curated English words across 5 tiers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing VocabWord records before seeding.",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            count = VocabWord.objects.count()
            VocabWord.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Deleted {count} existing records."))

        created_count = 0
        updated_count = 0

        for row in WORDS:
            word_val = row.pop("word")
            obj, created = VocabWord.objects.update_or_create(
                word=word_val,
                defaults=row,
            )
            row["word"] = word_val  # restore for re-runs
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done — {created_count} created, {updated_count} updated. "
                f"Total: {VocabWord.objects.count()} words."
            )
        )

        # Summary by tier
        for tier in range(1, 6):
            n = VocabWord.objects.filter(tier=tier).count()
            self.stdout.write(f"  Tier {tier}: {n} words")

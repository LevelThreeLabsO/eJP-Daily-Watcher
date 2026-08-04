#!/usr/bin/env python3
"""
Curated calendar sources for "What We're Watching".

Two halves, deliberately:

  CONVENINGS — the recurring institutional gatherings a national readership
  expects to see flagged: the GA, JFN, the denominational biennials and
  conventions, Kinus, the campus and day-school assemblies.

  CULTURE — where the offbeat items live. The editor specifically wants the
  Yiddish appreciation gala, not only AIPAC, and those come from museums, film
  festivals, Yiddish institutions, theaters and music festivals. Local
  federation calendars are excluded on purpose: they are 90% baby yoga.
"""

CONVENINGS = [
 # umbrella / federation system
 ("Jewish Federations of North America", "jewishfederations.org", "umbrella"),
 ("Conference of Presidents", "conferenceofpresidents.org", "umbrella"),
 ("Jewish Funders Network", "jfunders.org", "funder"),
 ("Jewish Communal Service Association", "jcsana.org", "professional"),
 ("Network of Jewish Human Service Agencies", "njhsa.org", "professional"),
 ("Association of Jewish Aging Services", "ajas.org", "professional"),
 ("Leading Edge", "leadingedge.org", "professional"),
 ("Upstart", "upstartlab.org", "professional"),
 ("SRE Network", "srenetwork.org", "professional"),
 ("Slingshot", "slingshotfund.org", "funder"),
 # advocacy / policy
 ("AIPAC", "aipac.org", "advocacy"),
 ("AJC", "ajc.org", "advocacy"),
 ("ADL", "adl.org", "advocacy"),
 ("Jewish Council for Public Affairs", "jewishpublicaffairs.org", "advocacy"),
 ("Israeli-American Council", "israeliamerican.org", "advocacy"),
 ("StandWithUs", "standwithus.com", "advocacy"),
 ("Zioness", "zioness.org", "advocacy"),
 ("Jewish Democratic Council", "jewishdems.org", "advocacy"),
 ("Republican Jewish Coalition", "rjchq.org", "advocacy"),
 ("Israel Policy Forum", "israelpolicyforum.org", "advocacy"),
 ("J Street", "jstreet.org", "advocacy"),
 ("Secure Community Network", "securecommunitynetwork.org", "security"),
 # denominations & rabbinic
 ("Union for Reform Judaism", "urj.org", "denomination"),
 ("Central Conference of American Rabbis", "ccarnet.org", "denomination"),
 ("United Synagogue of Conservative Judaism", "uscj.org", "denomination"),
 ("Rabbinical Assembly", "rabbinicalassembly.org", "denomination"),
 ("Orthodox Union", "ou.org", "denomination"),
 ("Rabbinical Council of America", "rabbis.org", "denomination"),
 ("Agudath Israel of America", "agudah.org", "denomination"),
 ("Chabad Kinus Hashluchim", "kinus.com", "denomination"),
 ("Reconstructing Judaism", "reconstructingjudaism.org", "denomination"),
 ("Jewish Reconstructionist", "ritualwell.org", "denomination"),
 # education / campus
 ("Prizmah", "prizmah.org", "education"),
 ("Foundation for Jewish Camp", "jewishcamp.org", "education"),
 ("Hillel International", "hillel.org", "campus"),
 ("Chabad on Campus", "chabadoncampus.org", "campus"),
 ("iCenter for Israel Education", "theicenter.org", "education"),
 ("Jewish Education Project", "jewishedproject.org", "education"),
 ("M2 Institute", "m2institute.org", "education"),
 ("Shalom Hartman Institute", "hartman.org.il", "education"),
 ("Pardes Institute", "pardes.org.il", "education"),
 ("Hadar Institute", "hadar.org", "education"),
 ("Yeshiva University", "yu.edu", "education"),
 ("Jewish Theological Seminary", "jtsa.edu", "education"),
 ("Hebrew Union College", "huc.edu", "education"),
 ("Brandeis Cohen Center", "brandeis.edu", "education"),
 # Israel / global
 ("Jewish Agency for Israel", "jewishagency.org", "israel"),
 ("JDC", "jdc.org", "israel"),
 ("Jewish National Fund USA", "jnf.org", "israel"),
 ("Birthright Israel", "birthrightisrael.com", "israel"),
 ("Masa Israel Journey", "masaisrael.org", "israel"),
 ("Nefesh B'Nefesh", "nbn.org.il", "israel"),
 ("Z3 Project", "z3project.org", "israel"),
 ("World Zionist Organization", "wzo.org.il", "israel"),
 ("Israel Bonds", "israelbonds.com", "israel"),
 ("Maccabi USA", "maccabiusa.com", "israel"),
 # women / service / other national
 ("Hadassah", "hadassah.org", "national"),
 ("National Council of Jewish Women", "ncjw.org", "national"),
 ("Jewish Women's Foundation Network", "jwfn.org", "national"),
 ("Moishe House", "moishehouse.org", "young adult"),
 ("OneTable", "onetable.org", "young adult"),
 ("Repair the World", "werepair.org", "service"),
 ("Avodah", "avodah.net", "service"),
 ("HIAS", "hias.org", "service"),
 ("Keshet", "keshetonline.org", "service"),
 ("Adamah", "adamah.org", "service"),
 ("JCC Association", "jcca.org", "jcc"),
 ("Jewish Federations Professional Institute", "jewishfederations.org", "professional"),
]

CULTURE = [
 # museums
 ("Jewish Museum (NY)", "thejewishmuseum.org", "museum"),
 ("Museum of Jewish Heritage", "mjhnyc.org", "museum"),
 ("Weitzman National Museum of American Jewish History", "weitzmanmuseum.org", "museum"),
 ("Contemporary Jewish Museum", "thecjm.org", "museum"),
 ("Skirball Cultural Center", "skirball.org", "museum"),
 ("US Holocaust Memorial Museum", "ushmm.org", "museum"),
 ("Illinois Holocaust Museum", "ilholocaustmuseum.org", "museum"),
 ("Museum of Tolerance", "museumoftolerance.com", "museum"),
 ("Center for Jewish History", "cjh.org", "archive"),
 ("YIVO Institute", "yivo.org", "archive"),
 ("Yiddish Book Center", "yiddishbookcenter.org", "archive"),
 ("Leo Baeck Institute", "lbi.org", "archive"),
 ("American Jewish Historical Society", "ajhs.org", "archive"),
 ("Jewish Women's Archive", "jwa.org", "archive"),
 ("National Library of Israel", "nli.org.il", "archive"),
 # performing arts / Yiddish / music
 ("National Yiddish Theatre Folksbiene", "nytf.org", "theater"),
 ("Yiddish New York", "yiddishnewyork.com", "yiddish"),
 ("KlezKanada", "klezkanada.org", "music"),
 ("Ashkenaz Festival", "ashkenaz.ca", "music"),
 ("Jewish Music Forum", "jewishmusicforum.org", "music"),
 ("Milken Archive of Jewish Music", "milkenarchive.org", "music"),
 ("Theater J", "theaterj.org", "theater"),
 ("Jewish Plays Project", "jewishplaysproject.org", "theater"),
 # film festivals
 ("SF Jewish Film Festival", "jfi.org", "film"),
 ("Atlanta Jewish Film Festival", "ajff.org", "film"),
 ("Boston Jewish Film", "bostonjewishfilm.org", "film"),
 ("Miami Jewish Film Festival", "miamijewishfilmfestival.org", "film"),
 ("Toronto Jewish Film Festival", "tjff.com", "film"),
 ("Israel Film Center", "israelfilmcenter.org", "film"),
 # books / ideas
 ("Jewish Book Council", "jewishbookcouncil.org", "books"),
 ("Limmud North America", "limmudna.org", "ideas"),
 ("Limmud", "limmud.org", "ideas"),
 ("Tablet", "tabletmag.com", "ideas"),
 ("Sefaria", "sefaria.org", "ideas"),
 ("Jewish Currents", "jewishcurrents.org", "ideas"),
 ("92NY Jewish programming", "92ny.org", "culture"),
 ("Eden Village", "edenvillagecamp.org", "culture"),
 ("Jewish Food Society", "jewishfoodsociety.org", "culture"),
 ("Beit T'Shuvah", "beittshuvah.org", "culture"),
]

ALL = [(n, d, k) for n, d, k in CONVENINGS + CULTURE]

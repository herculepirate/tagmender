# Scripting guide

TagMender has a small script language for two jobs:

- **naming**: a file naming script says where each file goes and what it is called;
- **tagging**: tagger scripts change the tags before they are written.

A script is plain text with tags such as `%album%` and functions such as `$upper(...)` mixed in.
This guide covers the syntax, the variables, every function (each with an example) and some
recipes. Every example here was run in TagMender, and the results shown are the ones it gives.

Contents: [Where scripts are used](#where-scripts-are-used) · [Syntax](#syntax) ·
[TagMender's own variables](#tagmenders-own-variables) · [Tags you can use](#tags-you-can-use) ·
[Functions](#functions) · [Recipes](#recipes) · [Troubleshooting](#troubleshooting)

## Where scripts are used

### The file naming script

Each profile has one naming script. It runs once for each file and gives the file's new path
**below the profile's destination folder**:

- `/` starts a new folder: `%albumartist%/%album%/%title%` gives three parts, two folders and a
  file name;
- the result has **no extension**: TagMender adds the file's own (`.flac`, `.mp3` ...);
- the tags it sees are the **new** ones, as they will be written after identification (and after
  any tagger scripts).

Most profiles use a ready-made layout: **Profiles › a profile › Settings › Target › Names** has
*Folder layout* (the presets), *Shorten album folder names*, *Editions in folder names* and
*Edition in the Album tag*. See [Naming presets](PROFILES.md#naming-presets).

To write your own, open **Tools › Script Editor…** (Ctrl+Shift+E) with the profile selected. Its
list has the naming script first. A naming script of the profile's own is then used in place of
the preset (it is kept in the profile as `naming.template`; empty means "use the preset").

The naming script never changes tags. `$set` and the other assignment functions only set a
variable for the rest of the script.

### Tagger scripts

Tagger scripts also live in **Tools › Script Editor…**. Add one with **+** (Ctrl+N), remove it
with **−** (Delete), and switch each on or off. *Run tagger scripts* turns them all on or off.
They run in list order, on each file's new tags, before the naming script. What they `$set`,
`$delete` or `$unset` is what gets written. Any text they give out is ignored; only what the
functions do counts.

A variable whose name starts with `_` is never written as a tag. A tagger script can set one
(`$set(_shelf,Ambient)`) and the naming script can read it (`%_shelf%`).

### The Script Editor

- **Preview** (Ctrl+1): runs every script on the selected album's planned tags (a sample track
  when no album is selected). It shows the new file name and the tags the tagger scripts change,
  and updates as you type.
- **Function Help** (Ctrl+2): what the function under the cursor does. The **Scripting Guide** link
  under it opens this page.
- Type `$` or `%`, or press Ctrl+Space, to complete function and variable names.
- An error is underlined where it is, with its line and column.
- **Save Scripts** writes the profile only, never a music file. Your music changes only through the
  next preview you apply, as with every change.

## Syntax

A script has three parts: text, variables and functions.

- **Text** is kept as it is: `Disc ` is the five characters D, i, s, c and a space.
- **Variables** are names between percent signs: `%artist%`. A tag that is not set is empty.
- **Functions** start with `$` and take arguments in brackets, separated by commas:
  `$left(%album%,10)`. Arguments can hold text, variables and other functions.

```
$upper(%album%)
```

With the album "Rainforest" this gives `RAINFOREST`.

### Several values

A tag can hold several values (two artists, three genres). `%genre%` joins them with `; `
("Ambient; Drone"). The [multi-value functions](#multi-value) work on the values one by one.

### Spaces and line breaks

Spaces count everywhere, also inside arguments: `$if(%genre%, Yes)` gives " Yes" with a space.
In the naming script, line breaks and tabs are dropped, so you can spread a long script over
several lines. Spaces at the start and end of each folder and file name are removed.

### True and false

There is no true or false value. Empty text is false; any other text (even a space) is true. The
condition functions give `1` for true and an empty text for false.

### Escaping

To use one of `\ $ % ( ) ,` as plain text, put a backslash before it:

```
$set(_price,\(\$1\,000\,000\))%_price%  → "($1,000,000)"
```

A tag whose name is not a valid variable name (it has a hyphen, say) can be read with `$get`:
`$get(re-recorded)`.

### Comments

`$noop(...)` does nothing and gives nothing. Use it for comments, or to switch a part off:

```
$noop( Folder: album artist, then the album )
%albumartist%/%album%/%title%
```

## TagMender's own variables

The naming script also sees these:

| Variable | Value |
|---|---|
| `%_year%` | The year of the original release date (`originalyear`, else `originaldate`), else of `date` |
| `%_albumartists_sorted%` | The album artists (the `albumartists` tag) A to Z; else the album artist as written. Use with `$join` |
| `%_edition%` | The edition: the reissue year and the kind, "2019 Vinyl", "2015 WEB", "2014 CD"; just "Vinyl" for an original vinyl; empty for an original CD and when the media is unknown |
| `%_edition_kind%` | The kind alone: "Vinyl", "WEB", "Tape"; empty for a CD |
| `%_multiartist%` | `1` when the release's tracks are by more than one artist |

And TagMender's own function [`$words(text,n)`](#wordstextn): the first *n* words.

Other hidden variables (they start with `_` and are never written):

- the file: `%_filename%` (no extension), `%_extension%`, `%_dirname%`, `%_filepath%`,
  `%_filesize%`, `%_format%`, `%_bitrate%`, `%_sample_rate%`, `%_channels%`, `%_bits_per_sample%`;
- the release: `%_albumartists%`, `%_releasecomment%`, `%_totalalbumtracks%`,
  `%_releasegroup_firstreleasedate%`, `%_primaryreleasetype%`, `%_secondaryreleasetype%`,
  `%_video%` (`1` for a video track).

## Tags you can use

Every tag TagMender writes can be read by its name in lower case. The Script Editor completes them
all. The ones used most:

| Tag | Holds |
|---|---|
| `%album%` | Album title |
| `%albumartist%`, `%albumartists%` | Album artist as credited; the album artists one by one |
| `%artist%`, `%artists%` | Track artist as credited; the track artists one by one |
| `%albumartistsort%`, `%artistsort%` | Sort names ("Cure, The") |
| `%title%` | Track title |
| `%tracknumber%`, `%totaltracks%` | Track number; tracks on this disc |
| `%discnumber%`, `%totaldiscs%` | Disc number; discs in the release |
| `%discsubtitle%` | The disc's own title |
| `%date%`, `%originaldate%`, `%originalyear%` | This release's date; the first release's date and year |
| `%genre%` | Genres (several values) |
| `%label%`, `%catalognumber%`, `%barcode%` | Label, catalogue number, barcode |
| `%media%` | "CD", "12\" Vinyl", "Digital Media" ... |
| `%releasetype%`, `%releasestatus%`, `%releasecountry%` | "album", "live" ...; "official" ...; "GB" ... |
| `%compilation%` | `1` for a various-artists compilation |
| `%composer%`, `%conductor%`, `%lyricist%`, `%performer:guitar%` ... | Credits |
| `%language%`, `%script%` | Language and script of the titles |
| `%albumversion%` | The version the album title names ("Remaster 2024") |

### Identifier tags

IDs that name the exact release, artist or recording. They are handy for telling two pressings
apart or for a check such as `$if(%musicbrainz_albumid%,...)`:

`%musicbrainz_albumid%` (release), `%musicbrainz_releasegroupid%`, `%musicbrainz_albumartistid%`,
`%musicbrainz_artistid%`, `%musicbrainz_recordingid%`, `%musicbrainz_trackid%`,
`%musicbrainz_workid%`, `%musicbrainz_discid%`, `%acoustid_id%`, `%isrc%`.

## Functions

Each function below has its usage, what it does and examples. In the examples, `→` shows what the
line gives; `""` is an empty (false) result. Arguments in square brackets are optional.

Groups: [Assignment](#assignment) · [Text](#text) · [Multi-value](#multi-value) ·
[Numbers](#numbers) · [Conditions and logic](#conditions-and-logic) · [Dates](#dates) ·
[Information and other](#information-and-other)

### Assignment

In a tagger script these change the tags that are written. In the naming script they only change
variables for the rest of the script.

#### `$set(name,value)`

Sets the variable `name` to `value`. A name starting with `_` is never written as a tag. When
`name` is itself a variable (`%_base%`), its value is used as the name.

```
$set(_x,Testing)%_x%  → "Testing"
$set(_base,_target)$set(%_base%,Hello)%_target%  → "Hello"
```

#### `$setmulti(name,value[,separator])`

Sets `name` to several values, splitting `value` at `separator` (`; ` when not given).

```
$setmulti(_x,A; B; C)$lenmulti(%_x%)  → "3"
$setmulti(_x,A:A; B:B,:)$lenmulti(%_x%)  → "3"
```

#### `$unset(name)`

Removes the variable `name`. `$unset(performer:*)` removes every performer tag (the `*` works with
`performer:`, `comment:` and `lyrics:`).

```
$set(_x,1)$unset(_x)%_x%  → ""
```

#### `$delete(name)`

Like `$unset`, but in a tagger script the tag is also removed from the file when it is written.
`$delete(comment)` removes the comment tag.

```
$set(_x,1)$delete(_x)%_x%  → ""
```

#### `$copy(target,source)`

Copies `source` to `target`, keeping several values as several values (`$set(target,%source%)`
would join them into one). Give the names without `%`. `target` is overwritten.

```
$setmulti(_src,one; two)$copy(_dst,_src)$lenmulti(%_dst%)  → "2"
```

#### `$copymerge(target,source[,keep_duplicates])`

Adds the values of `source` to the end of `target`, leaving out values `target` already has (unless
`keep_duplicates` is not empty). Give the names without `%`.

```
$setmulti(_t,zero; two)$setmulti(_s,one; two)$copymerge(_t,_s)%_t%  → "zero; two; one"
$setmulti(_t,zero; two)$setmulti(_s,one; two)$copymerge(_t,_s,1)%_t%  → "zero; two; one; two"
```

#### `$get(name)`

The value of the variable `name`, as `%name%`; empty when it is not set. When `name` is a variable,
its value is used as the name.

```
$set(_foo,This is foo)$get(_foo)  → "This is foo"
$set(_foo,This is foo)$set(_bar,_foo)$get(%_bar%)  → "This is foo"
$get(_baz)  → ""
```

#### `$get_new(name)`

The new value of `name`. A TagMender script sees one set of tags, the new ones, so this gives the
same as `$get`.

```
$set(_foo,baz)$get_new(_foo)  → "baz"
```

#### `$get_original(name)`

The value of `name` as the script sees it. In TagMender this gives the same as `$get`: the new
tags. (Your files' old tags are shown beside the new ones in the inspector.)

```
$set(_foo,baz)$get_original(_foo)  → "baz"
```

### Text

#### `$upper(text)`, `$lower(text)`

`text` in upper case, or in lower case.

```
$upper(This text is UPPER case)  → "THIS TEXT IS UPPER CASE"
$lower(tHe houR is upOn uS)  → "the hour is upon us"
```

#### `$title(text)`

`text` with the first letter of every word in upper case. Other letters are left alone, so "BBC"
stays "BBC"; use `$lower` first to change those too.

```
$title(tHe houR is upOn uS)  → "THe HouR Is UpOn US"
$title($lower(tHe houR is upOn uS))  → "The Hour Is Upon Us"
```

#### `$left(text,number)`

The first `number` characters of `text`. A negative number counts from the end.

```
$left(ABC,2)  → "AB"
$left(ABC,4)  → "ABC"
$left(ABC,-2)  → "A"
$left(2001-05-01,4)  → "2001"
```

#### `$right(text,number)`

The last `number` characters of `text`. A negative number counts from the start.

```
$right(abcd,2)  → "cd"
$right(abcd,-1)  → "bcd"
$right(abcd,-5)  → ""
```

#### `$substr(text,start[,end])`

The characters from index `start` up to (not including) `end`. Indexes start at 0; negative ones
count from the end. A blank `start` is the beginning, a blank or missing `end` the end.

```
$substr(abcdefg,3)  → "defg"
$substr(abcdefg,,3)  → "abc"
$substr(abcdefg,-3)  → "efg"
$substr(abcdefg,-6,3)  → "bc"
```

#### `$len(text)`

The number of characters in `text`.

```
$len(ABC)  → "3"
$len()  → "0"
```

#### `$find(haystack,needle)`

Where `needle` first appears in `haystack`, counting from 0; empty when it is not there. Case
matters.

```
$find(abcdef,c)  → "2"
$find(abcdef,cd)  → "2"
$find(abcdef,B)  → ""
```

#### `$replace(text,search,replace)`

`text` with every `search` replaced by `replace`.

```
$replace(I like cats the best,cat,dog)  → "I like dogs the best"
$replace(I like cats the best,cat,)  → "I like s the best"
$replace(AC/DC,/,-)  → "AC-DC"
```

#### `$rreplace(text,pattern,replace)`

Like `$replace`, with a regular expression as `pattern`. Backslashes, `$`, commas and brackets
in the pattern need a backslash before them (see [Escaping](#escaping)).

```
$rreplace(test \(disc 1\),\\s\\\(disc \\d+\\\),)  → "test"
$rreplace(Live at the BBC   ,\\s+\$,)  → "Live at the BBC"
```

#### `$rsearch(text,pattern[,group])`

The first match of the regular expression `pattern` in `text`. With a group in brackets, only
what the first group matched; `group` picks another one by number. Empty when nothing matches.

```
$rsearch(test \(disc 1\),\\\(disc \(\\d+\)\\\))  → "1"
$rsearch(test,x)  → ""
$rsearch(disc: 3,\(\\w+\): \(\\d+\),2)  → "3"
```

#### `$trim(text[,characters])`

`text` without spaces at its start and end; or, with `characters`, without any of those
characters there.

```
$trim(  Trimmed  )  → "Trimmed"
$trim(__Trimmed__,_)  → "Trimmed"
$trim(x__Trimmed__y,_x)  → "Trimmed__y"
```

#### `$strip(text)`

`text` with every run of spaces, tabs and line breaks made one space, and none at the start or end.

```
$strip(  This  text has been stripped.  )  → "This text has been stripped."
```

#### `$truncate(text,length)`

`text` cut to `length` characters. A negative length counts from the end.

```
$truncate(Once upon a time,3)  → "Onc"
$truncate(Once upon a time,-3)  → "Once upon a t"
```

#### `$firstwords(text,length)`

Like `$truncate`, but keeps only whole words that fit in `length` characters.

```
$firstwords(Once upon a time,7)  → "Once"
$firstwords(Once upon a time,-3)  → "Once upon a"
$firstwords(Once upon a time,0)  → ""
```

#### `$words(text,n)`

TagMender's own: the first `n` words of `text` (`$firstwords` counts characters, this counts
words). When the album title ends in the album's version in brackets (the `albumversion` tag,
"(Remaster 2024)"), that part is always kept, so two versions of an album keep folders of their
own.

```
$words(Soma The Twentieth Anniversary Edition,3)  → "Soma The Twentieth"
$words(Rainforest,5)  → "Rainforest"
$set(albumversion,Remaster 2024)$words(Dark Side of the Moon \(Remaster 2024\),2)  → "Dark Side (Remaster 2024)"
```

#### `$initials(text)`

The first character of each word that starts with a letter.

```
$initials(This is a test)  → "Tiat"
$initials(This is a 123 test)  → "Tiat"
$initials($upper(Touch The Earth))  → "TTE"
```

#### `$firstalphachar(text[,nonalpha])`

The first character of `text` in upper case. When it is not a letter, `nonalpha` instead (`#` when
not given).

```
$firstalphachar(abc)  → "A"
$firstalphachar(123)  → "#"
$firstalphachar(123,0-9)  → "0-9"
```

#### `$delprefix(text[,prefixes])`

`text` without the first of `prefixes` it starts with (several, separated by commas; "A" and "The"
when not given). Case matters.

```
$delprefix(The Beatles)  → "Beatles"
$delprefix(THE Beatles)  → "THE Beatles"
$delprefix(THE Beatles,THE)  → "Beatles"
```

#### `$swapprefix(text[,prefixes])`

Moves the prefix from the start of `text` to its end ("A" and "The" when not given). Case matters.

```
$swapprefix(The Cure)  → "Cure, The"
$swapprefix(Le Butcherettes)  → "Le Butcherettes"
$swapprefix(Le Butcherettes,A,An,The,Le)  → "Butcherettes, Le"
```

#### `$num(number,length)`

`number` with leading zeros up to `length` digits (at most 20). Text that is not a whole number
counts as 0.

```
$num(3,2)  → "03"
$num(123,5)  → "00123"
$num(123,1)  → "123"
$num(a,5)  → "00000"
```

#### `$pad(text,length,character)`

`text` with `character` added at its start until it is `length` characters long. `character`
should be one character.

```
$pad(abc,5,+)  → "++abc"
$pad(abc,0,+)  → "abc"
```

#### `$reverse(text)`

`text` back to front.

```
$reverse(abcde)  → "edcba"
```

### Multi-value

These work on a variable with several values. `name` is usually given as `%name%`. You can also
give a list as text; it is split at `separator` (`; ` when not given).

#### `$join(name,text[,separator])`

The values of `name` with `text` between them.

```
$setmulti(_bar,First:A; Second:B)$join(%_bar%, >> )  → "First:A >> Second:B"
$join(First:A; Second:B, >> ,:)  → "First >> A; Second >> B"
$join(Steve Roach; Robert Rich, & )  → "Steve Roach & Robert Rich"
```

#### `$getmulti(name,index[,separator])`

The value at `index` (from 0; negative counts from the end). Empty when there is none.

```
$setmulti(_bar,A; B; C)$getmulti(%_bar%,0)  → "A"
$setmulti(_bar,A; B; C)$getmulti(%_bar%,-1)  → "C"
$getmulti(A:1; B:2; C:3,10)  → ""
```

#### `$lenmulti(name[,separator])`

How many values `name` has.

```
$setmulti(_foo,A; B; C)$lenmulti(%_foo%)  → "3"
$lenmulti(A:A; B:B; C:C,:)  → "4"
$lenmulti(,)  → "0"
```

#### `$slice(name,start[,end[,separator]])`

The values from index `start` up to (not including) `end`. Indexes start at 0; negative ones count
from the end.

```
$slice(A; B; C; D; E,1,3)  → "B; C"
$slice(A; B; C; D; E,,3)  → "A; B; C"
$slice(A; B; C; D; E,1,-1)  → "B; C; D"
```

#### `$sortmulti(name[,separator])`

The values sorted A to Z.

```
$sortmulti(B; D; E; A; C)  → "A; B; C; D; E"
```

#### `$reversemulti(name[,separator])`

The values in reverse order. With `$sortmulti`, sorts Z to A.

```
$reversemulti(A; B; C; D; E)  → "E; D; C; B; A"
$reversemulti($sortmulti(B; A; C))  → "C; B; A"
```

#### `$unique(name[,case_sensitive[,separator]])`

The values sorted, each one once. Case is ignored unless `case_sensitive` is not empty.

```
$unique(a; A; B; b; cd; Cd; cD; CD; a; A; b)  → "A; CD; b"
$unique(a; A; B; b; cd; Cd; cD; CD; a; A; b,1)  → "A; B; CD; Cd; a; b; cD; cd"
```

#### `$cleanmulti(name)`

Removes empty values from the variable `name` (give the name without `%`).

```
$setmulti(_test,One; ; Two; Three)$cleanmulti(_test)%_test%  → "One; Two; Three"
```

#### `$replacemulti(name,search,replace[,separator])`

Replaces each value equal to `search` with `replace`. Empty values are dropped.

```
$setmulti(_foo,Electronic; Idm; Techno)$replacemulti(%_foo%,Idm,IDM)  → "Electronic; IDM; Techno"
$setmulti(_foo,Four; Five; Six)$replacemulti(%_foo%,Five,)  → "Four; Six"
```

#### `$map(name,code[,separator])`

Runs `code` on each value and gives the results. In `code`, `%_loop_value%` is the value and
`%_loop_count%` its number (from 1). Empty results are dropped.

```
$setmulti(_bar,First:A; Second:B)$map(%_bar%,$upper(%_loop_count%=%_loop_value%))  → "1=FIRST:A; 2=SECOND:B"
$setmulti(_baz,A; B; x; C)$map(%_baz%,$if($eq(%_loop_value%,x),,%_loop_count%=%_loop_value%))  → "1=A; 2=B; 4=C"
```

#### `$foreach(name,code[,separator])`

Runs `code` once for each value, with `%_loop_value%` and `%_loop_count%` as in `$map`. Gives
nothing itself; use it for what `code` does.

```
$setmulti(_artists,Ann; Bob)$foreach(%_artists%,$set(_artist_%_loop_count%,%_loop_value%))%_artist_2%  → "Bob"
```

In a tagger script, `$foreach(genre; comment,$delete(%_loop_value%))` removes both tags.

#### `$performer(pattern[,separator])`

The performers whose role matches `pattern`, separated by `separator` (", " when not given). The
role is matched anywhere and case matters. A blank `pattern` gives every performer.
`/pattern/` is a regular expression; `/pattern/i` ignores case.

```
$set(performer:guitar,Ann)$set(performer:rhythm-guitar,Bob)$performer(guitar)  → "Ann, Bob"
$set(performer:guitar,Ann)$set(performer:rhythm-guitar,Bob)$performer(/^guitar/)  → "Ann"
$set(performer:guitar,Ann)$set(performer:drums,Cindy)$performer(, / )  → "Ann / Cindy"
```

### Numbers

These take whole numbers. When an argument is empty or not a whole number, they give an empty
result.

#### `$add(x,y,...)`, `$sub(x,y,...)`, `$mul(x,y,...)`

Add, subtract or multiply, with as many numbers as you like.

```
$add(20,15)  → "35"
$add(20,10,3,2)  → "35"
$sub(20,15)  → "5"
$mul(1,2,3)  → "6"
$add(20,3.5)  → ""
```

#### `$div(x,y,...)`

Divides, rounding down. Dividing by 0 gives an empty result.

```
$div(10,3)  → "3"
$div(10,-3)  → "-4"
$div(10,2,0)  → ""
```

#### `$mod(x,y,...)`

The remainder after dividing.

```
$mod(10,3)  → "1"
$mod(-13,10)  → "7"
```

#### `$max(type,x,...)`, `$min(type,x,...)`

The largest or smallest value. `type` is `int`, `float`, `text` (case matters), `nocase` or
`auto` (blank: `auto`, which uses `int`, then `float`, then `text`, the first that fits every
value). A list as text counts value by value.

```
$max(int,1,2)  → "2"
$max(float,1,2,3.1)  → "3.1"
$max(text,2020-01-01,2020-01-02,2020-02)  → "2020-02"
$min(int,5,4; 6; 3)  → "3"
$min(nocase,a,B,c)  → "a"
```

### Conditions and logic

#### `$if(condition,then[,else])`

`then` when `condition` is not empty, else `else` (empty when not given).

```
$set(_foo,This is foo)$if(%_foo%,%_foo%,No foo)  → "This is foo"
$if(%_bar%,This is bar,No bar)  → "No bar"
$if(,True,False)  → "False"
$if( ,True,False)  → "True"
```

#### `$if2(a1,a2,...)`

The first argument that is not empty.

```
$set(_bar,Something)$if2(%_foo%,%_bar%,Three)  → "Something"
$if2(%_foo%,,Three)  → "Three"
```

#### `$not(x)`

True when `x` is empty.

```
$not(%_foo%)  → "1"
$not(x)  → ""
```

#### `$and(x,y,...)`, `$or(x,y,...)`

`$and`: true when every argument is not empty. `$or`: true when any one is not empty.

```
$and(x,1)  → "1"
$and(x,)  → ""
$and(x,$lt(4,5))  → "1"
$or(,1)  → "1"
$or(,)  → ""
```

#### `$eq(x,y)`, `$ne(x,y)`

Equal, or not equal. Case matters.

```
$eq(a,a)  → "1"
$eq(a,A)  → ""
$ne(a,A)  → "1"
```

#### `$eq_any(x,a1,a2,...)`, `$eq_all(x,a1,a2,...)`

True when `x` equals any one, or every one, of the others.

```
$eq_any(A,A,B,C)  → "1"
$eq_any(A,a,b,c)  → ""
$eq_all(A,A,A,A)  → "1"
$eq_all(A,A,B,C)  → ""
```

#### `$ne_any(x,a1,a2,...)`, `$ne_all(x,a1,a2,...)`

True when `x` differs from any one, or from every one, of the others.

```
$ne_any(A,A,B,C)  → "1"
$ne_any(A,A,A,A)  → ""
$ne_all(A,a,a,a)  → "1"
$ne_all(A,A,B,C)  → ""
```

#### `$gt(x,y[,type])`, `$gte(x,y[,type])`, `$lt(x,y[,type])`, `$lte(x,y[,type])`

Greater than, greater or equal, less than, less or equal. `type` is as for
[`$max`](#maxtypex-minx): numbers are compared as numbers unless you say otherwise.

```
$gt(10,9)  → "1"
$gt(6,6)  → ""
$gte(6,6)  → "1"
$lt(6.5,6.6)  → "1"
$lte(B,a,text)  → "1"
$lt(a,B,nocase)  → "1"
$gt(6.1,4,int)  → ""
```

#### `$in(x,y)`

True when `x` contains `y`. Case matters; use `$lower` on both for a match that ignores case.

```
$in(Black Metal,Metal)  → "1"
$in(Black Metal,metal)  → ""
$in($lower(Black Metal),metal)  → "1"
```

#### `$inmulti(%x%,y)`

True when one of the values of `x` is exactly `y`.

```
$setmulti(_foo,One; Two; Three)$inmulti(%_foo%,Two)  → "1"
$setmulti(_foo,One; Two; Three)$inmulti(%_foo%,w)  → ""
```

#### `$is_multi(x)`

True when `x` has more than one value.

```
$setmulti(_baz,a; b; c)$is_multi(%_baz%)  → "1"
$is_multi(a)  → ""
```

#### `$startswith(text,prefix)`, `$endswith(text,suffix)`

True when `text` starts with `prefix`, or ends with `suffix`. Case matters.

```
$startswith(The time is now.,The time)  → "1"
$startswith(The time is now.,The TIME)  → ""
$endswith(The time is now,is now)  → "1"
```

#### `$while(condition,code)`

Runs `code` again and again while `condition` is true, at most 1000 times. `condition` is
checked before each run; `%_loop_count%` is the number of runs so far.

```
$set(_r,Echo...)$while($lt(%_loop_count%,2),$set(_r,%_r% echo...))%_r%  → "Echo... echo... echo..."
```

### Dates

A date is year, month and day as numbers, with anything that is not a digit between them
("2021-07-21", "2021.07.21", "2021 07 21"). `date_order` says their order: `ymd` (the default),
`dmy` or `mdy`. An invalid date gives an empty result.

#### `$year(date[,date_order])`, `$month(date[,date_order])`, `$day(date[,date_order])`

The year, month or day part of `date`, as written.

```
$year(2020-07-21)  → "2020"
$month(2020-07-21)  → "07"
$month(2020-7-21)  → "7"
$day(07.21.2020,mdy)  → "21"
$year(2020-07)  → "2020"
```

#### `$dateformat(date[,format[,date_order]])`

`date` written as `format`, which uses the strftime codes (`%Y` year, `%m` month, `%d` day, `%B`
month name ...). Put a backslash before each `%` and comma in `format`. Without `format`:
"2021-07-21". `date` needs a year, a month and a day.

```
$dateformat(2021-7-21)  → "2021-07-21"
$dateformat(2021-7-21,\%B \%d\, \%Y)  → "July 21, 2021"
$dateformat(07.21.2021,\%Y.\%m.\%d,mdy)  → "2021.07.21"
$dateformat(2021-07)  → ""
```

#### `$datetime([format])`

The date and time now, as `format` (the same codes as `$dateformat`; default
"2026-02-15 14:26:32"). Put a backslash before each `%`. Avoid it in a naming script: the name
would change from one run to the next.

```
$len($datetime(\%Y))  → "4"
```

### Information and other

#### `$noop(...)`

Does nothing and gives nothing; what is inside is not run. For comments.

```
$noop( A comment. )  → ""
$noop($set(_foo,Testing))%_foo%  → ""
```

#### `$countryname(country_code[,translate])`

The English name of a country from its two-letter code; empty for an unknown code. `translate` is
accepted, but the name is always in English.

```
$countryname(ca)  → "Canada"
$countryname(GB)  → "United Kingdom"
$countryname(INVALID)  → ""
```

#### `$is_audio()`, `$is_video()`

Whether the track is a music track or a video (from `%_video%`).

```
$is_audio()  → "1"
$is_video()  → ""
```

#### `$is_complete()`, `$matchedtracks()`

TagMender names files one at a time, without an album's count of matched tracks, so
`$is_complete()` is always false and `$matchedtracks()` always gives 0. They are there so that
scripts that use them still run.

```
$is_complete()  → ""
$matchedtracks()  → "0"
```

## Recipes

Each recipe is a naming script, with the tags it was tried on and the path TagMender gave (the
extension is added after it). Type the script on one line, or over several: line breaks are
dropped.

### Year - Album folders

```
%albumartist%/$if(%_year%,%_year% - )%album%/$num(%tracknumber%,2) %title%
```

With album artist "Robert Rich", album "Rainforest", track 3 "Tower", date 2001-05-01 and original
date 1989-11-01: `Robert Rich/1989 - Rainforest/03 Tower`. With no date at all:
`Robert Rich/Rainforest/03 Tower`.

### Albums into folders by genre, under one music root

Ambient albums go to `ambient/`, metal and grindcore to `extreme/`, the rest to `other/`, all below
the profile's one destination:

```
$if($in(%genre%,Ambient),ambient,$if($or($in(%genre%,Metal),$in(%genre%,Grind)),extreme,other))/%albumartist%/%album%/$num(%tracknumber%,2) %title%
```

With album artist "A", album "B", track 3 "T":

| Genre | Path |
|---|---|
| Black Metal | `extreme/A/B/03 T` |
| Dark Ambient | `ambient/A/B/03 T` |
| Ambient; Drone (two values) | `ambient/A/B/03 T` |
| Jazz | `other/A/B/03 T` |

`$in` is case-sensitive. To match "black metal" too, compare in lower case:

```
$set(_g,$lower(%genre%))
$if($in(%_g%,ambient),Ambient,$if($or($in(%_g%,metal),$in(%_g%,grind)),Extreme,Other))/
%albumartist%/%album%/$num(%tracknumber%,2) %title%
```

Genre "black metal": `Extreme/Robert Rich/Rainforest/03 Tower`.

Keep the genre tests in the first folder: an album whose tracks have different genres would
otherwise be split. If your music already sits in one inbox folder per genre, a profile's sources
can instead each have their own destination (see `sources` in [Profiles](PROFILES.md#settings)).

### Multi-disc albums

A `Disc 02` folder only when there is more than one disc:

```
%albumartist%/%album%/$if($gt(%totaldiscs%,1),Disc $num(%discnumber%,2)/)$num(%tracknumber%,2) %title%
```

Disc 2 of 2: `Robert Rich/Rainforest/Disc 02/03 Tower`. One disc: `Robert Rich/Rainforest/03 Tower`.

Or the disc number in the file name, all in one folder:

```
%albumartist%/%album%/$if($gt(%totaldiscs%,1),%discnumber%-)$num(%tracknumber%,2) %title%
```

Disc 2 of 2: `Robert Rich/Rainforest/2-03 Tower`.

### First-letter folders

```
$firstalphachar($if2(%albumartistsort%,%albumartist%))/%albumartist%/%album%/$num(%tracknumber%,2) %title%
```

| Album artist (sort name) | Path |
|---|---|
| Robert Rich (Rich, Robert) | `R/Robert Rich/Rainforest/03 Tower` |
| The Cure (no sort name) | `T/The Cure/Disintegration/01 Plainsong` |
| 808 State | `#/808 State/90/01 Pacific` |

To file "The Cure" under C without a sort name, drop the prefix first:
`$firstalphachar($delprefix(%albumartist%))/...` gives `C/The Cure/Disintegration/01 Plainsong`.

### Various Artists

Compilations in one `Various Artists` folder, with each track's artist in its file name:

```
$if(%compilation%,Various Artists,$if2(%albumartist%,%artist%))/%album%/$num(%tracknumber%,2) $if(%_multiartist%,%artist% - )%title%
```

A compilation (track 4, "An Ending" by Brian Eno, on "Ambient 2"):
`Various Artists/Ambient 2/04 Brian Eno - An Ending`. An album by one artist:
`Robert Rich/Rainforest/03 Tower`.

### Collaborations, short album folders and editions

The `tagmender` preset's style, with the album cut to three words:

```
$join(%_albumartists_sorted%, & )/$if(%_year%,%_year% - )$words(%album%,3)$if(%_edition%, [%_edition%])/$num(%tracknumber%,2).$initials($upper(%title%))
```

With album artists "Steve Roach" and "Robert Rich", album "Soma The Twentieth Anniversary Edition",
original date 1992, this pressing from 2012 on 12" vinyl, track 3 "Touch The Earth":
`Robert Rich & Steve Roach/1992 - Soma The Twentieth [2012 Vinyl]/03.TTE`.

### Tagger scripts

A tagger script changes the tags that are written. Tried on the tags shown:

| Script | Before | After |
|---|---|---|
| `$setmulti(genre,$lower(%genre%))` | genre "Black Metal; Ambient" | genre "black metal; ambient" |
| `$delete(comment)` | comment "ripped by x" | no comment tag |
| `$set(albumartistsort,$swapprefix(%albumartist%,The))` | album artist "The Cure" | album artist sort "Cure, The" |
| `$if($eq(%releasetype%,live),$set(album,%album% \(Live\)))` | album "Pulse", release type "live" | album "Pulse (Live)" |

## Troubleshooting

- **"Unknown function"**: a misspelt or unknown function is an error when the profile is checked
  and in the Script Editor (`1:0:$foo: Unknown function '$foo'`); it is never quietly left empty.
  The Script Editor colours unknown functions red.
- **"Unexpected end of script"**: a bracket or a `%` is not closed. The editor marks the matching
  bracket under the cursor.
- **A `/` in a tag** becomes `_` in the path, so it cannot make a folder: "AC/DC" gives
  `AC_DC/Back in Black/Hells Bells`. Only a `/` in the script itself starts a folder. To use
  another character, `$replace(%albumartist%,/,-)`.
- **Empty parts are dropped**: when a folder part comes out empty, there is no empty folder.
  `%albumartist%/%label%/%album%/%title%` with no label gives `Robert Rich/Rainforest/Tower`.
- **A script ending in `/`** names folders only: each file keeps its own file name in them.
- **"Wrong number of arguments"**: `$if(a,b,c,d)` gives `Wrong number of arguments for $if:
  Expected between 2 and 3, got 4`. A comma inside a function's arguments starts a new argument;
  write `\,` for a plain comma.
- **Text where you did not expect it**: spaces count. `$if(%genre%, Yes)` gives " Yes".
- **Several values joined**: `%artist%` with two artists is "A; B". Use `$join(%artists%, & )` or
  `$getmulti(%artists%,0)` to choose.
- **Case**: `$eq`, `$in`, `$startswith` and the others match case exactly. Compare `$lower(...)`
  of both sides when case should not matter.
- **Check before you apply**: the Script Editor's Preview, or `tagmender plan PATH --profile NAME`,
  shows the new names without changing anything.

# -*- coding: UTF-8 -*-

from __future__ import absolute_import
from __future__ import print_function

import re
from datetime import date

from pychess.compat import basestring
from pychess.System import conf
from pychess.System.Log import log
from pychess.Utils.Board import Board
from pychess.Utils.lutils.LBoard import LBoard
from pychess.Utils.GameModel import GameModel
from pychess.Utils.lutils.lmove import toSAN
from pychess.Utils.Move import Move
from pychess.Utils.const import *
from pychess.Utils.logic import getStatus
from pychess.Utils.lutils.ldata import MATE_VALUE
from pychess.Utils import prettyPrintScore
from pychess.Variants import name2variant, NormalBoard
from pychess.widgets.ChessClock import formatTime

from .pgnbase import PgnBase, pgn_load
from .ChessFile import LoadingError


__label__ = _("Chess Game")
__ending__ = "pgn"
__append__ = True

moveeval = re.compile("\[%eval ([+\-])?(?:#)?(\d+)(?:[,\.](\d{1,2}))?(?:/(\d{1,2}))?\]")
movetime = re.compile("\[%emt (\d):(\d\d):(\d\d)(?:\.(\d\d\d))?\]")

def wrap (string, length):
    lines = []
    last = 0
    while True:
        if len(string)-last <= length:
            lines.append(string[last:])
            break
        i = string[last:length+last].rfind(" ")
        lines.append(string[last:i+last])
        last += i + 1
    return "\n".join(lines)

def msToClockTimeTag (ms):
    """ 
    Converts milliseconds to a chess clock time string in 'WhiteClock'/
    'BlackClock' PGN header format
    """
    msec = ms % 1000
    sec = ((ms - msec) % (1000 * 60)) / 1000
    min = ((ms - sec*1000 - msec) % (1000*60*60)) / (1000*60)
    hour = ((ms - min*1000*60 - sec*1000 - msec) % (1000*60*60*24)) / (1000*60*60)
    return "%01d:%02d:%02d.%03d" % (hour, min, sec, msec)

def parseClockTimeTag (tag):
    """ 
    Parses 'WhiteClock'/'BlackClock' PGN headers and returns the time the
    player playing that color has left on their clock in milliseconds
    """
    match = re.match("(\d{1,2}):(\d\d):(\d\d).(\d\d\d)", tag)
    if match:
        hour, min, sec, msec = match.groups()
        return int(msec) + int(sec)*1000 + int(min)*60*1000 + int(hour)*60*60*1000

def parseTimeControlTag (tag):
    """ 
    Parses 'TimeControl' PGN header and returns the time and gain the
    players have on game satrt in seconds
    """
    match = re.match("(\d+)(?:\+(\d+))?", tag)
    if match:
        secs, gain = match.groups()
        return int(secs), int(gain) if gain is not None else 0
    
def save (file, model, position=None):

    status = u"%s" % reprResult[model.status]

    print(u'[Event "%s"]' % model.tags["Event"], file=file)
    print(u'[Site "%s"]' % model.tags["Site"], file=file)
    print(u'[Date "%04d.%02d.%02d"]' % \
        (int(model.tags["Year"]), int(model.tags["Month"]), int(model.tags["Day"])), file=file)
    print(u'[Round "%s"]' % model.tags["Round"], file=file)
    print(u'[White "%s"]' % repr(model.players[WHITE]), file=file)
    print(u'[Black "%s"]' % repr(model.players[BLACK]), file=file)
    print(u'[Result "%s"]' % status, file=file)
    if "ECO" in model.tags:
        print(u'[ECO "%s"]' % model.tags["ECO"], file=file)
    if "WhiteElo" in model.tags:
        print(u'[WhiteElo "%s"]' % model.tags["WhiteElo"], file=file)
    if "BlackElo" in model.tags:
        print(u'[BlackElo "%s"]' % model.tags["BlackElo"], file=file)
    if "TimeControl" in model.tags:
        print(u'[TimeControl "%s"]' % model.tags["TimeControl"], file=file)
    if "Time" in model.tags:
        print(u'[Time "%s"]' % str(model.tags["Time"]), file=file)
    if model.timed:
        print(u'[WhiteClock "%s"]' % \
            msToClockTimeTag(int(model.timemodel.getPlayerTime(WHITE) * 1000)), file=file)
        print(u'[BlackClock "%s"]' % \
            msToClockTimeTag(int(model.timemodel.getPlayerTime(BLACK) * 1000)), file=file)

    if model.variant.variant != NORMALCHESS:
        print(u'[Variant "%s"]' % model.variant.cecp_name.capitalize(), file=file)

    if model.boards[0].asFen() != FEN_START:
        print(u'[SetUp "1"]', file=file)
        print(u'[FEN "%s"]' % model.boards[0].asFen(), file=file)
    print(u'[PlyCount "%s"]' % (model.ply-model.lowply), file=file)
    if "EventDate" in model.tags:
        print(u'[EventDate "%s"]' % model.tags["EventDate"], file=file)
    if "Annotator" in model.tags:
        print(u'[Annotator "%s"]' % model.tags["Annotator"], file=file)
    print(u"", file=file)

    result = []
    walk(model.boards[0].board, result, model)
            
    result = u" ".join(result)
    result = wrap(result, 80)
    print(result, status, file=file)
    print(u"", file=file)
    file.close()

def walk(node, result, model, vari=False):
    """Prepares a game data for .pgn storage.
       Recursively walks the node tree to collect moves and comments
       into a resulting movetext string.
       
       Arguments:
       node - list (a tree of lboards created by the pgn parser)
       result - str (movetext strings)"""

    enhanced_save = conf.get("enhanced_save_check", False)
    
    def store(text):
        if len(result) > 1 and result[-1] == "(":
            result[-1] = "(%s" % text
        elif text == ")":
            result[-1] = "%s)" % result[-1]
        else:
            result.append(text)

    while True: 
        if node is None:
            break
        
        # Initial game or variation comment
        if node.prev is None:
            for child in node.children:
                if isinstance(child, basestring):
                    store("{%s}" % child)
            node = node.next
            continue

        movecount = move_count(node, black_periods=enhanced_save and "TimeControl" in model.tags)
        if movecount is not None:
            if movecount:
                store(movecount)
            move = node.lastMove
            store(toSAN(node.prev, move))
            if enhanced_save and not vari:
                emt_eval = ""
                if "TimeControl" in model.tags:
                    elapsed = model.timemodel.getElapsedMoveTime(node.plyCount - model.lowply)
                    emt_eval = "[%%emt %s]" % formatTime(elapsed, clk2pgn=True)
                if node.plyCount in model.scores:
                    moves, score, depth = model.scores[node.plyCount]
                    emt_eval += "[%%eval %0.2f/%s]" % (score, depth)
                if emt_eval:
                    store("{%s}" % emt_eval)

        for nag in node.nags:
            if nag:
                store(nag)

        for child in node.children:
            if isinstance(child, basestring):
                child = re.sub("\[%.*?\]", "", child)
                # comment
                if child:
                    store("{%s}" % child)
            else:
                # variations
                if node.fen_was_applied:
                    store("(")
                    walk(child[0], result, model, vari=True)
                    store(")")
                    # variation after last played move is not valid pgn
                    # but we will save it as in comment
                else:
                    store("{Analyzer's primary variation:")
                    walk(child[0], result, model, vari=True)
                    store("}")

        if node.next:
            node = node.next
        else:
            break

def move_count(node, black_periods=False):
    mvcount = None
    if node.fen_was_applied:
        ply = node.plyCount
        if ply % 2 == 1:
            mvcount = "%d." % (ply//2+1)
        elif node.prev.prev is None or node != node.prev.next or black_periods:
            # initial game move, or initial variation move
            mvcount = "%d..." % (ply//2)
        elif node.prev.children:
            # move after real(not [%foo bar]) comment
            need_mvcount = False
            for child in node.prev.children:
                if isinstance(child, basestring):
                    if not child.startswith("[%"):
                        need_mvcount = True
                        break
                else:
                    need_mvcount = True
                    break
            if need_mvcount:
                mvcount = "%d..." % (ply//2)
            else:
                mvcount = ""
        else:
            mvcount = ""        
    return mvcount


def load(file):
    return pgn_load(file, klass=PGNFile)


class PGNFile (PgnBase):

    def __init__ (self, games):
        PgnBase.__init__(self, games)

    def loadToModel (self, gameno, position=-1, model=None):
        if not model:
            model = GameModel()

        # the seven mandatory PGN headers
        model.tags['Event'] = self._getTag(gameno, 'Event')
        model.tags['Site'] = self._getTag(gameno, 'Site')
        model.tags['Date'] = self._getTag(gameno, 'Date')
        model.tags['Round'] = self.get_round(gameno)
        model.tags['White'], model.tags['Black'] = self.get_player_names(gameno)
        model.tags['Result'] = reprResult[self.get_result(gameno)]
        
        pgnHasYearMonthDay = True
        for tag in ('Year', 'Month', 'Day'):
            if not self._getTag(gameno, tag):
                pgnHasYearMonthDay = False
                break
        if model.tags['Date'] and not pgnHasYearMonthDay:
            date_match = re.match(".*(\d{4}).(\d{2}).(\d{2}).*", model.tags['Date'])
            if date_match:
                year, month, day = date_match.groups()
                model.tags['Year'] = year
                model.tags['Month'] = month
                model.tags['Day'] = day
                
        # non-mandatory headers
        for tag in ('Annotator', 'ECO', 'EventDate', 'Time', 'WhiteElo', 'BlackElo', 'TimeControl'):
            if self._getTag(gameno, tag):
                model.tags[tag] = self._getTag(gameno, tag)
            else:
                model.tags[tag] = ""

        # TODO: enable this when NewGameDialog is altered to give user option of
        # whether to use PGN's clock time, or their own custom time. Also,
        # dialog should set+insensitize variant based on the variant of the
        # game selected in the dialog
        if model.tags['TimeControl']:
            secs, gain = parseTimeControlTag(model.tags['TimeControl'])
            model.timed = True
            model.timemodel.secs = secs
            model.timemodel.gain = gain
            model.timemodel.minutes = secs / 60

            for tag, color in (('WhiteClock', WHITE), ('BlackClock', BLACK)):
                if self._getTag(gameno, tag):
                    try:
                        ms = parseClockTimeTag(self._getTag(gameno, tag))
                        model.timemodel.intervals[color][0] = ms / 1000
                    except ValueError: 
                        raise LoadingError( \
                            "Error parsing '%s' Header for gameno %s" % (tag, gameno))
        
        fenstr = self._getTag(gameno, "FEN")
        variant = self.get_variant(gameno)
        
        if variant:
            model.tags["Variant"] = variant
            # Fixes for some non statndard Chess960 .pgn
            if (fenstr is not None) and variant == "Fischerandom":
                parts = fenstr.split()
                parts[0] = parts[0].replace(".", "/").replace("0", "")
                if len(parts) == 1:
                    parts.append("w")
                    parts.append("-")
                    parts.append("-")
                fenstr = " ".join(parts)

            model.variant = name2variant[variant]
            board = LBoard(model.variant.variant)
        else:
            model.variant = NormalBoard
            board = LBoard()

        if fenstr:
            try:
                board.applyFen(fenstr)
            except SyntaxError as e:
                board.applyFen(FEN_EMPTY)
                raise LoadingError(_("The game can't be loaded, because of an error parsing FEN"), e.args[0])
        else:
            board.applyFen(FEN_START)
        
        boards = [board]

        del model.moves[:]
        del model.variations[:]
        
        self.error = None
        movetext = self.get_movetext(gameno)
        
        boards = self.parse_string(movetext, boards[0], position)

        # The parser built a tree of lboard objects, now we have to
        # create the high level Board and Move lists...
        
        for board in boards:
            if board.lastMove is not None:
                model.moves.append(Move(board.lastMove))
        
        self.has_emt = False
        self.has_eval = False
        
        def walk(node, path):
            if node.prev is None:
                # initial game board
                board = model.variant(setup=node.asFen(), lboard=node)
            else:
                move = Move(node.lastMove)
                try:
                    board = node.prev.pieceBoard.move(move, lboard=node)
                except:
                    raise LoadingError(_("Invalid move."), "%s%s" % (move_count(node, black_periods=True), move))

            if node.next is None:
                model.variations.append(path+[board])
            else:
                walk(node.next, path+[board])

            for child in node.children:
                if isinstance(child, list):
                    if len(child) > 1:
                        # non empty variation, go walk
                        walk(child[1], list(path))
                else:
                    if not self.has_emt:
                        self.has_emt = child.find("%emt") >= 0
                    if not self.has_eval:
                        self.has_eval = child.find("%eval") >= 0
        
        # Collect all variation paths into a list of board lists
        # where the first one will be the boards of mainline game.
        # model.boards will allways point to the current shown variation
        # which will be model.variations[0] when we are in the mainline.
        walk(boards[0], [])
        model.boards = model.variations[0]
        
        self.has_emt = self.has_emt and "TimeControl" in model.tags
        if self.has_emt or self.has_eval:
            if self.has_emt:
                blacks = len(model.moves)//2
                whites = len(model.moves)-blacks

                model.timemodel.intervals = [
                    [model.timemodel.intervals[0][0]]*(whites+1),
                    [model.timemodel.intervals[1][0]]*(blacks+1),
                ]
                secs, gain = parseTimeControlTag(model.tags['TimeControl'])
                model.timemodel.intervals[0][0] = secs
                model.timemodel.intervals[1][0] = secs
            
            for ply, board in enumerate(boards):
                for child in board.children:
                    if isinstance(child, basestring):
                        if self.has_emt:
                            match = movetime.search(child)
                            if match:
                                movecount, color = divmod(ply+1, 2)
                                hour, minute, sec, msec = match.groups()
                                prev = model.timemodel.intervals[color][movecount-1]
                                msec = 0 if msec is None else int(msec)
                                msec += int(sec)*1000 + int(minute)*60*1000 + int(hour)*60*60*1000
                                model.timemodel.intervals[color][movecount] = prev - msec/1000
                        
                        if self.has_eval:
                            match = moveeval.search(child)
                            if match:
                                sign, num, fraction, depth = match.groups()
                                sign = 1 if sign is None or sign == "+" else -1
                                num = int(num) if int(num) == MATE_VALUE else int(num)
                                fraction = 0 if fraction is None else float(fraction)/100
                                value = sign * (num + fraction)
                                depth = "" if depth is None else depth
                                model.scores[ply] = ("", value, depth)

            log.debug("pgn.loadToModel: intervals %s" % model.timemodel.intervals)

        # Find the physical status of the game
        model.status, model.reason = getStatus(model.boards[-1])
        
        # Apply result from .pgn if the last position was loaded
        if position == -1 or len(model.moves) == position - model.lowply:
            status = self.get_result(gameno)
            if status in (WHITEWON, BLACKWON) and status != model.status:
                model.status = status
                model.reason = WON_RESIGN
            elif status == DRAW and status != model.status:
                model.status = DRAW
                model.reason = DRAW_AGREE
        
        # If parsing gave an error we throw it now, to enlarge our possibility
        # of being able to continue the game from where it failed.
        if self.error:
            raise self.error

        return model

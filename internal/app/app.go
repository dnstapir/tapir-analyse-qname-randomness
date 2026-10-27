package app

import (
	"context"
	"math"
	"strings"
	"sync"
	"time"
	"unicode"

	"github.com/dnstapir/tapir-analyse-lib/common"
	"github.com/dnstapir/tapir-analyse-lib/libtapir"

	"github.com/dnstapir/tapir-analyse-dga-scorer/internal/classifier"
)

const c_N_HANDLERS = 3
const c_NATS_DELIM = common.NATS_DELIM

type Conf struct {
	Debug    bool `toml:"debug"`
	Interval int  `toml:"interval"`
	classifier.Conf
	AnalystID  string
	Log        common.Logger
	NatsHandle nats
}

type appHandle struct {
	id               string
	log              common.Logger
	classifierHandle *classifier.Classifier
	natsHandle       nats
	ticker           *time.Ticker
	exitCh           chan<- common.Exit
	pm
}

type pm struct {
}

type job struct {
	isTick   bool
	tickData int64
	msg      common.NatsMsg
}

type nats interface {
	ActivateSubscription(context.Context) (<-chan common.NatsMsg, error)
	SetObservation(context.Context, string, string) error
	Shutdown() error
}

func Create(conf Conf) (*appHandle, error) {
	a := new(appHandle)

	if conf.Log == nil {
		return nil, common.ErrBadHandle
	}
	a.log = conf.Log

	if conf.NatsHandle == nil {
		return nil, common.ErrBadHandle
	}
	a.natsHandle = conf.NatsHandle

	if conf.AnalystID == "" {
		a.log.Error("Bad analyst ID during creation")
		return nil, common.ErrBadParam
	}
	a.id = conf.AnalystID

	c, err := classifier.Create(conf.Conf)
	if err != nil {
		a.log.Error("Could not create classifier: %s", err)
		return nil, err
	}
	a.classifierHandle = c

	if conf.Interval > 0 {
		a.ticker = time.NewTicker(time.Duration(conf.Interval) * time.Second)
	} else {
		a.ticker = time.NewTicker(time.Duration(math.MaxInt32) * time.Second)
		a.ticker.Stop()
		a.log.Warning("No interval set. Periodic tasks disabled.")
	}

	a.log.Debug("Main app debug logging enabled")
	return a, nil
}

func (a *appHandle) Run(ctx context.Context, exitCh chan<- common.Exit) {
	defer a.ticker.Stop()

	var natsChan <-chan common.NatsMsg
	var err error
	a.exitCh = exitCh
	jobChan := make(chan job, 10)

	natsChan, err = a.natsHandle.ActivateSubscription(ctx)
	if err != nil {
		a.log.Error("Couldn't activate NATS subscription: '%s'", err)
		a.exitCh <- common.Exit{ID: a.id, Err: err}
		return
	}

	var wg sync.WaitGroup
	for range c_N_HANDLERS {
		wg.Go(func() {
			for j := range jobChan {
				a.handleJob(ctx, j)
			}
			a.log.Info("Worker done!")
		})
	}

MAIN_APP_LOOP:
	for {
		select {
		case t := <-a.ticker.C:
			a.log.Debug("Tick")
			j := job{
				isTick:   true,
				tickData: t.Unix(),
			}
			select {
			case jobChan <- j:
			case <-ctx.Done():
				break MAIN_APP_LOOP
			}
		case natsMsg, ok := <-natsChan:
			if !ok {
				a.log.Warning("NATS channel closed")
				natsChan = nil
			} else {
				a.log.Debug("Incoming NATS message")
				j := job{
					msg: natsMsg,
				}
				select {
				case jobChan <- j:
				case <-ctx.Done():
					break MAIN_APP_LOOP
				}
			}
		case <-ctx.Done():
			a.log.Info("Stopping main worker thread")
			break MAIN_APP_LOOP
		}
	}

	close(jobChan)

	wg.Wait()

	err = a.natsHandle.Shutdown()
	if err != nil {
		a.log.Error("Encountered '%s' during NATS shutdown", err)
	}

	a.exitCh <- common.Exit{ID: a.id, Err: err}
	a.log.Info("Main app shutdown done")
	return
}

func (a *appHandle) handleJob(ctx context.Context, j job) {
	if j.isTick {
		a.handleTick(ctx, j.tickData)
	} else {
		a.handleMsg(ctx, j.msg)
	}
}

func (a *appHandle) handleTick(ctx context.Context, epoch int64) {
	a.log.Debug("Received tick '%d' (no periodic work configured)", epoch)
}

func (a *appHandle) handleMsg(ctx context.Context, msg common.NatsMsg) {
	a.log.Debug("Handling %d byte message on subject %s", len(msg.Data), msg.Subject)
	if len(msg.Data) <= 0 {
		a.log.Warning("Msg had no data, probably garbage. Won't handle...")
		return
	}

	msgDomain, err := libtapir.ExtractDomain(msg.Data)
	if err != nil {
		a.log.Error("Error reading domain from message: %s", err)
		return
	}
	a.log.Debug("Extracted domain %q", msgDomain)

	if strings.Contains(msgDomain, "_") {
		a.log.Debug("Domain %q contains underscore, skipping", msgDomain)
		return
	}

	if !libtapir.HasValidETLD(msgDomain) {
		a.log.Debug("Domain %q did not have valid eTLD, ignoring...", msgDomain)
		return
	}

	etldPlusOne, err := libtapir.GetETLDPlusOne(msgDomain)
	if err != nil {
		a.log.Debug("Could not get eTLD+1 for %q: %s", msgDomain, err)
		return
	}

	if msgDomain != etldPlusOne {
		a.log.Debug("Domain %q is deeper than eTLD+1 %q, skipping", msgDomain, etldPlusOne)
		return
	}

	label := leftmostLabel(etldPlusOne)
	if label == "" {
		a.log.Debug("Could not extract leftmost label from %q, skipping", etldPlusOne)
		return
	}

	if isIDN(label) || isIDN(msgDomain) {
		a.log.Debug("Domain %q (label %q) looks like IDN, skipping", msgDomain, label)
		return
	}

	class, score, err := a.classifierHandle.Classify(label)
	if err != nil {
		a.log.Error("Error classifying %q (label %q): %s", msgDomain, label, err)
		return
	}

	a.log.Debug("Domain %q label %q score %f class %s", msgDomain, label, score, class)

	if class == classifier.LikelyDGA {
		a.log.Info("Likely DGA: domain %q label %q score %f", msgDomain, label, score)
	} else {
		a.log.Debug("Not DGA: domain %q label %q score %f", msgDomain, label, score)
	}
}

func leftmostLabel(etldPlusOne string) string {
	trimmed := strings.Trim(etldPlusOne, ".")
	if trimmed == "" {
		return ""
	}
	label, _, _ := strings.Cut(trimmed, ".")
	return label
}

func isIDN(name string) bool {
	for _, r := range name {
		if r > unicode.MaxASCII {
			return true
		}
	}
	for _, part := range strings.Split(strings.Trim(name, "."), ".") {
		if strings.HasPrefix(part, "xn--") {
			return true
		}
	}
	return false
}

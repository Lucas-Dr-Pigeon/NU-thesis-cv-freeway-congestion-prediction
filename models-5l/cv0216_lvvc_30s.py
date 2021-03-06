# -*- coding: utf-8 -*-

# -*- coding: utf-8 -*-

# -*- coding: utf-8 -*-
"""
Created on Sun Dec 27 23:13:33 2020

@author: Lucas
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import preprocessing as prep
import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from main_full_lane import get_windows, clean_res
from torch.utils.data.dataset import Dataset
from torch.utils.data import DataLoader
from run_NN import run_NN
from run_NN_online import run_NN_online
from sklearn.ensemble import RandomForestClassifier
import statistics as stats
import random
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures
import datetime


''' you can import and manipulate the dataset as an object '''
class cv_data():
    def __init__(self):
        ''' specify dataset file paths '''
        self.paths = [
            r"vehicle-trajectory-data\0750am-0805am\trajectories-0750am-0805am.csv",
            r"vehicle-trajectory-data\0805am-0820am\trajectories-0805am-0820am.csv",
            r"vehicle-trajectory-data\0820am-0835am\trajectories-0820am-0835am.csv"
            ]
        
    def __Import_Data__(self):
        data = []
        for path in self.paths:
            data.append(pd.read_csv(path))
        ''' store the trajectory records into the class'''
        self.data = pd.concat(data).reset_index(drop=True)
   
        ''' unix_time '''
        self.unix_time = np.asarray(sorted(set(self.data["Global_Time"])))
        
        ''' convert unix_time to date '''
        datetimes = []
        for t in self.unix_time:
            datetimes.append(self.__datetime__(t))
        self.datetimes = np.asarray(datetimes)  
        
        ''' report the study period '''
        self.period = ( self.unix_time[-1] - self.unix_time[0] ) / 100
        print ("Study Period: " + str(self.datetimes[0]) + " - " + str(self.datetimes[-1]))
        print ("Period Length: " + str( int( self.period /600 )) + " min " 
                                 + str( int( self.period %600 /10 )) + " s " 
                                 + str( int( self.period %600 %10 )) + " ms " )
        
        ''' report the study segment '''
        all_Y = np.asarray(sorted(set(self.data["Local_Y"])))
        self.segmentlength = all_Y[-1] - all_Y[0]
        print ("Study Segment Length: " + str(self.segmentlength) + " ft ")
        
        ''' store all the vehicles '''
        self.vehicles = np.asarray(sorted(set(self.data["Vehicle_ID"]))) 
        print ("Number of Vehicles: " + str(self.vehicles.shape[0]))
        
        ''' see how many lanes '''
        self.lanes = len(sorted(set(self.data["Lane_ID"]))) 
        print ("Number of Lanes: " + str(self.lanes))
        
        ''' return the data so that it could be called by another function '''
        return self.data

    def __datetime__(self, time):
        s = datetime.datetime.fromtimestamp(time/1000.0).strftime('%Y-%m-%d %H:%M:%S.%f')
        return s
    
    def __sample__(self, num = 500, thres = 80, mpr = 50, balance = True, window_size = 600, zone_size = 400):
        
        df = self.data
        
        ''' the global range of time and position '''
        T = [1118847100000, 1118847100000 + 650000]
        X = [100, 1600]
        
        ''' sampling according to mpr '''
        g = self.data.groupby("Vehicle_ID")
        v_num = len(g)
        cv = df[df["Vehicle_ID"].isin(random.sample(set(df["Vehicle_ID"].unique()), int (len(g) * mpr / 100) ))]
        
        print ("Market Penetration: " + str(len(cv.groupby("Vehicle_ID"))) + "/" + str (len(g)) ) 
        
        ''' initialize these lists '''
        q = []
        k = []
        sdc_cr = np.zeros((0, window_size))
        vel_cr = np.zeros((0, window_size))
        std_cr = np.zeros((0, window_size))
        lvvc_cr = np.zeros((0, window_size))
        v_cr = []
       
        sdc_ds = np.zeros((0, window_size))
        vel_ds = np.zeros((0, window_size))
        std_ds = np.zeros((0, window_size))
        lvvc_ds = np.zeros((0, window_size))
        v_ds = []
        label = []
        num_false = 0
        num_true = 0
        zones = []
        windows = []
        ssd_cr = []
        ssd_ds = []
        
        while num_false < num or num_true < num :
            t = random.randint(T[0] / 100, T[1] / 100) * 100
            x = random.randint(X[0], X[1])
          
            ''' section to predict '''
            sec_pd = df [ (df['Local_Y'] <  x + zone_size) &
                          (df['Local_Y'] >=  x ) &
                          (df['Global_Time'] >= t + 20000 ) &
                          (df['Global_Time'] < 	t + 30000 ) &
                          (df['Lane_ID'] < 6 )            ]
            res_pd = get_elements(sec_pd, [t+20000, t+30000], [x, x + zone_size])
            k_pd = res_pd["density"]
            status = False if k_pd < thres else True

            
            ''' current section '''
            sec_cr = cv [ (cv['Local_Y'] <  x + zone_size) &
                          (cv['Local_Y'] >=  x) &
                          (cv['Global_Time'] >= t - window_size * 100 ) &
                          (cv['Global_Time'] < 	t  ) &
                          (cv['Lane_ID'] < 6 )            ]
            res_cr = get_elements(sec_cr, [t - window_size * 100, t], [x, x + zone_size], plot = 0)

            ''' downstream section '''
            sec_ds = cv [ (cv['Local_Y'] <  x + zone_size * 2) &
                          (cv['Local_Y'] >=  x + zone_size) &  
                          (cv['Global_Time'] >= t - window_size * 100) &
                          (cv['Global_Time'] < 	t  ) &
                          (cv['Lane_ID'] < 6 )            ]
            res_ds = get_elements(sec_ds, [t - window_size * 100, t], [x + zone_size, x + zone_size * 2], plot = 0)
            
            ''' if a time-series is complete '''
            if len(res_cr["vels"]) != window_size or len(res_ds["vels"]) != window_size or len(res_cr["stds"]) != window_size or len(res_ds["stds"]) != window_size:
                print ( len(res_cr["vels"]), len(res_ds["vels"]), len(res_cr["lvvcs"]), len(res_ds["lvvcs"]))
                continue
            
            ''' reject if there are so many samples with that status '''
            if num_false < num and status == False:
                num_false += 1
            elif num_true < num and status == True:
                num_true += 1
            else:
                continue
            ''' append everything you need to your lists '''
            label.append(status)
            q_pd = res_pd["flow"]
            q.append(q_pd)
            k.append(k_pd)
            v_cr.append(res_cr["speed"])
            v_ds.append(res_ds["speed"])
            
            ssd_cr.append(res_cr["ssd"])
            ssd_ds.append(res_ds["ssd"])
            
            vel_cr = np.append(vel_cr, [res_cr["vels"]], axis = 0)
            sdc_cr = np.append(sdc_cr, [res_cr["stds"]], axis = 0)
            std_cr = np.append(std_cr, [res_cr["stds"]], axis = 0)
            lvvc_cr = np.append(lvvc_cr, [res_cr["lvvcs"]], axis = 0)
            
            vel_ds = np.append(vel_ds, [res_ds["vels"]], axis = 0)
            sdc_ds = np.append(sdc_ds, [res_cr["stds"]], axis = 0)
            std_ds = np.append(std_ds, [res_ds["stds"]], axis = 0)
            lvvc_ds = np.append(lvvc_ds, [res_ds["lvvcs"]], axis = 0)
            
            zones.append([ x, x + 400 ])
            windows.append([ t + 20000, t + 30000 ] )
            
            print ("Progress " + str(num_false + num_true) + "/" + str(num * 2) + ": " + str(zones[-1]) + ", " + str(windows[-1]))
        ''' print q-k plot ''' 
        q = np.asarray(q)
        k = np.asarray(k)
        label = np.asarray(label)
            
        plt.scatter(k[np.where(label == True)], q[np.where(label == True)], color="red", s= 1)
        plt.scatter(k[np.where(label == False)], q[np.where(label == False)], color="blue", s= 1)
        plt.xlim(0, 150)
        plt.ylim(0, 2500)
        now = datetime.datetime.now()
        dt_string = now.strftime("%d_%m_%Y_%H_%M_%S")
            
        plt.savefig("./images/qk" + str(dt_string), dpi=300)
        plt.show()

        ''' dump samples_ssd to a df '''
        samples_ssd = pd.DataFrame(zip(v_cr, ssd_cr, v_ds, ssd_ds, label, zones, windows), columns = ["current speed", "current ssd", "downstream speed", "dowmstream ssd", "status", "zone", "window"])
        
        ''' dump samples_lvvc to a df ''' 
        samples_lvvc = pd.DataFrame(zip(np.array(vel_cr), np.array(lvvc_cr), np.array(vel_ds), np.array(lvvc_ds), label, zones, windows), columns = ["current speed series", "current lvvc series", "downstream speed series", "dowmstream lvvc series", "status", "zone", "window"])
        
        print (vel_cr.shape, lvvc_cr.shape, vel_ds.shape, lvvc_ds.shape )
        # samples_sdc = pd.DataFrame(zip(np.array(vel_cr), np.array(sdc_cr), np.array(vel_ds), np.array(sdc_ds)), columns = ["current speed series", "current sdc series", "downstream speed series", "dowmstream ssd series"])
        
        ''' save samples '''
        save_data(samples_ssd, "./data/ssd_samples_" +  str(mpr) + "_" + str(dt_string))
        
        ''' save samples '''
        save_data(samples_lvvc, "./data/sdc_samples_" +  str(mpr) + "_" + str(dt_string))
        
        self.samples_lvvc = samples_lvvc
        self.samples_ssd = samples_ssd
        
        return samples_lvvc, samples_ssd
    
    def __import_samples__(self, filepath):
        self.samples = load_data(filepath)
        
    def __cross_validation__(self, samples = None, K = 5):
         
        if samples is None:
            samples = np.asarray(self.samples.values[:,:7])
            
        else:
            samples = np.asarray(samples.values[:,:7])
            
        N = samples.shape[0]
        
        ''' get features '''
        features = np.zeros((N, 4, 600))
        for i in range(N):
            for j in range(len(samples[i][:4])):
                features[i][j] = samples[i][j]
                #features[i][j] = _smooth_(features[i][j])
                for k in range(len(features[i][j])):
                    features[i][j][k] = 0 if math.isnan(features[i][j][k]) else features[i][j][k]
                
        #print (features[58][:3])
        
        targets = samples[:,4]
        
        
        ''' normalize '''

        # features[:,0] /= 70
        # features[:,1] /= 2.5
        # features[:,2] /= 70 
        # features[:,3] /= 2.5 

        
        
        ''' training parameters '''
        n_epochs = int(2e4)
        stop_thr = 1e-14
        lr = 5e-3
            
        ''' training results '''
        accuracies = []
        all_accuracy = []
        test_accuracies = []
        test_cms = []
        test_f1 = []

        order = np.arange(N)
        np.random.shuffle(order)
        test_num = int (N / K / 2)
        
        for _k in range(K - 1):
            testid = order[_k * test_num : (_k + 1) * test_num]
            validid = order[(_k + 1) * test_num : (_k + 2) * test_num]
            trainid = np.setdiff1d(order, np.append(testid, validid, axis = 0))
 
            trainset = MyDataset(features[trainid][:,:,-600:], targets[trainid])
            validset = MyDataset(features[validid][:,:,-600:], targets[validid])
            testset = MyDataset(features[testid][:,:,-600:], targets[testid])
            
            _model,_loss,_accuracy,_test_accuracy,_test_cm, _test_f1 = run_NN(NN(),running_mode='train', train_set=trainset, valid_set=validset, test_set=testset, 
                                                           batch_size=1, learning_rate=lr, n_epochs=n_epochs, stop_thr=stop_thr, shuffle=True)
            
            accuracies.append(_accuracy["valid"][-1])
            all_accuracy.append([_accuracy["train"][-1], _accuracy["valid"][-1]])
            test_accuracies.append(_test_accuracy)
            test_cms.append(_test_cm)
            test_f1.append(_test_f1)
            
        ''' when _k = K '''
        testid = order[-test_num * 2 : -test_num ]
        validid = order[-test_num : ]
        trainid = np.setdiff1d(order, np.append(testid, validid, axis = 0))
        
        trainset = MyDataset(features[trainid], targets[trainid])
        validset = MyDataset(features[validid], targets[validid])
        testset = MyDataset(features[testid], targets[testid])
        
        _model,_loss,_accuracy,_test_accuracy,_test_cm, _test_f1 = run_NN(NN(), running_mode='train', train_set=trainset, valid_set=validset, test_set=testset, 
                                                           batch_size=1, learning_rate=lr, n_epochs=n_epochs, stop_thr=stop_thr, shuffle=True)
        
        accuracies.append(_accuracy["valid"][-1])
        all_accuracy.append([_accuracy["train"][-1], _accuracy["valid"][-1]])
        test_accuracies.append(_test_accuracy)
        test_cms.append(_test_cm)
        test_f1.append(_test_f1)
        
        print ("Final Accuracy: " + str(np.mean(accuracies)))
        return accuracies, _model, all_accuracy, test_accuracies, test_cms, round(np.mean(np.asarray(test_f1)) * 100, 2)
    
    
class MyDataset(Dataset):
    def __init__(self, x, y):
        self.x = np.asarray(x, dtype = float)
        self.y = np.asarray(y, dtype = float)
        
    def __getitem__(self, index):
        x = self.x[index]
        y = self.y[index]
        return (x, y)

    def __len__(self):
        return self.x.shape[0]        
            
def get_elements(df, window, zone, plot = False):
    g = df.groupby("Vehicle_ID")
    tx = g["Local_Y"].agg(np.ptp).sum()
    tt = g["Frame_ID"].agg(np.ptp).sum() + g["Frame_ID"].agg(np.ptp).count()
    # print (tx, tt)
    dx = max (g["Local_Y"].max().to_list()) - \
            min (g["Local_Y"].min().to_list())
    dt = max (g["Frame_ID"].max().to_list()) - \
            min (g["Frame_ID"].min().to_list()) + 1  
    gt = df.groupby("Global_Time")
    covs = []
    ts = []
    vels = []
    cors = []
    stds = []
    lvvcs = []
    # print (np.arange(ini_t, end_t, 100))
    diff = np.setdiff1d(np.arange(window[0], window[1], 100), np.asarray(list(gt.groups.keys())))
    
    # print (ini_t, end_t)
    # print (np.asarray(list(gt.groups.keys())))
    
    
    color = "red" if len(diff) else "orange"
    
    # print (np.arange(ini_t, end_t, 100)[np.where(np.arange(ini_t, end_t, 100) not in gt.groups)[0]])
    for t in np.arange(window[0], window[1], 100):
        x = (df[df["Global_Time"] == t]["Local_Y"].values - zone[0]) 
        
        v = df[df["Global_Time"] == t]["v_Vel"].values / 70
        # if not (np.corrcoef(df[df["Global_Time"] == t]["Local_Y"],df[df["Global_Time"] == t]["v_Vel"])[0][1]  
        #        == np.corrcoef(x,v)[0][1]):
        #     print (np.corrcoef(df[df["Global_Time"] == t]["Local_Y"],df[df["Global_Time"] == t]["v_Vel"])[0][1])
        #     print (np.corrcoef(x,v)[0][1])
        
        if math.isnan(np.std(v)):
            stds.append(0)
            lvvcs.append(0)
        else:
            stds.append( min(np.std(v * 70) / 15, 1))
            lvvcs.append( np.std(v * 70) / np.mean(v * 70) )
        
        if math.isnan(np.cov(x,v)[0][1]):
            try:
                covs.append(0)
            except IndexError:
                covs.append(0)
        else:
            covs.append(np.cov(x,v)[0][1] / 5)
            
            
        # print (x, v)
            
        if math.isnan(np.mean(v)):
            try:
                vels.append(vels[-1])
            except IndexError:
                vels.append(1)
        else:
            vels.append(np.mean(v))
        
        if math.isnan(np.corrcoef(x,v)[0][1]) or abs(np.corrcoef(x,v)[0][1]) == 1:
            try:
                cors.append(cors[-1])
            except IndexError:
                cors.append(0)
        else:
            cors.append(np.corrcoef(x,v)[0][1])
             
        ts.append(t)
        
    flow = tx / (dx * dt) * 3600 / 5 * 10
    density = tt / (dx * dt) * 5280 / 5 
    speed = flow / density * 5280 / 3600
        
    if plot:
        flow = tx / (dx * dt) * 3600 / 5 * 10
        density = tt / (dx * dt) * 5280 / 5 
        speed = flow / density * 5280 / 3600
        
        fig, ax1 = plt.subplots()
        ax1.plot(ts, np.asarray(vels) * 70, color = "green", linewidth = 1)
        ax1.set_ylim(0, 80)
        ax2 = ax1.twinx()
        ax2.plot(ts, _smooth_(covs), color = color, linewidth = 1)
        ax2.set_ylim(-1, 1)
        fig.show()

    
    ''' get ssd '''
    dx_list = np.asarray(g["Local_Y"].max().to_list()) - np.asarray(g["Local_Y"].min().to_list())
    dt_list = np.asarray(g["Frame_ID"].max().to_list()) - np.asarray(g["Frame_ID"].min().to_list()) + 1
    v_list = dx_list / dt_list * 10
    # print (dx_list, dt_list, v_list )
    ssd = np.std(v_list)
    
    return {"speed": speed, "density": density, "flow": flow, "covs": np.asarray(covs), "frames": ts, "ssd": ssd, "vels": np.asarray(vels), "cors":np.asarray(cors), "stds":np.asarray(stds), "lvvcs":np.asarray(lvvcs)}    

def _smooth_(y, box_pts=50):
    box = np.ones(box_pts)/box_pts
    y_smooth = np.convolve(y, box, mode='same')
    return y_smooth
    
def _now_():
    return datetime.datetime.now().time()

def save_data(data, filepath):
    import pickle
    with open(filepath, 'wb') as fp:
        pickle.dump(data, fp, protocol=pickle.HIGHEST_PROTOCOL)
                    
def load_data(filepath):
    import pickle
    with open(filepath, 'rb') as fp:
        data = pickle.load(fp)
    return data    

def print_sample(samp):
    features = samp[:4]
    status = samp[4]
    color = "red" if status else "blue"
    plt.plot(np.arange(samp[6][0] - 60000, samp[6][0], 100), _smooth_(features[3] ), color = color, linewidth = 1)
    # plt.plot(np.arange(300), features[3], color = color, linewidth = 1, linestyle = "dashed")
    # plt.ylim(-1, 1)
    plt.show()
    

class NN(nn.Module):
    def __init__(self):
        super(NN, self).__init__()    
        self.fc1 = nn.Linear(2400, 100)
        # self.fc2 = nn.Linear(200, 50)
        self.fc3 = nn.Linear(100, 2)    
        
    def forward(self, input):
        output = torch.tensor(input, dtype=torch.float)
        output = output.view(output.size(0),-1)
        output = self.fc1(output)
        # output = self.fc2(output)
        output = self.fc3(output)
        return output
def __task__():
       T = [tuple([t *10000 + 1118847100000, ( t + 1 ) *10000 + 1118847100000]) for t in np.arange(0,65)  ]
       vs = []
       ks = []
       covs = []
       cors = []
       steps = []
       frames = []
       ssds = []
       vels = []
       stds = []
       lvvcs = []
   
       zone = [590, 990]
       mpr = 30
   
       ''' sampling according to mpr '''
       df = us101_data
       g = df.groupby("Vehicle_ID")
       v_num = len(g)
       cv = df[df["Vehicle_ID"].isin(random.sample(set(df["Vehicle_ID"].unique()), int (len(g) * mpr / 100) ))]
       
       for window in T:
           print (window)
           sec_cv = cv[  (cv['Local_Y']  < zone[1]) &
                         (cv['Local_Y'] >= zone[0]) &
                         (cv['Global_Time'] >= window[0] ) &
                         (cv['Global_Time'] < 	window[1] ) &
                         (cv['Lane_ID'] < 6 )            ]
           sec_100 = df[ (df['Local_Y'] <   zone[1]) &
                         (df['Local_Y'] >=  zone[0]) &
                         (df['Global_Time'] >= window[0] ) &
                         (df['Global_Time'] < 	window[1] ) &
                         (df['Lane_ID'] < 6 )     ]            
           res_cv = get_elements(sec_cv, window, zone)
           res_100 = get_elements(sec_100, window, zone)
           vs.append(res_100["speed"])
           ks.append(res_100["density"])
           steps.append(  window[1] / 2 + window[0] / 2  )
           frames += res_100["frames"]
           covs = np.append( covs, res_cv["covs"] )  
           vels = np.append( vels, res_cv["vels"] )
           cors = np.append( cors, res_cv["cors"] )
           stds = np.append( stds, res_cv["stds"] )
           lvvcs = np.append( lvvcs, res_cv["lvvcs"] )
           ssds.append(res_cv["ssd"])
       
       fig, ax = plt.subplots()
       ax.scatter(steps, ks, color = "blue", zorder = 3)
       ax.plot(steps, ks, color = "black", linestyle = "dashed", zorder = 2)
       ax.scatter(steps, vs, color = "green", zorder = 3)
       ax.plot(steps, vs, color = "black", linestyle = "dashed", zorder = 2)
       ax.set_xlabel("Frames (0.1sec)")
       ax.set_ylabel("Density (veh/mi)", color = "blue")
       ax.set_ylim(0, 120)
       # ax.set_xlim(1000, 5000)
       # plt.title("Density changes over time")
       # plt.show()
       
       # covs = _smooth_(covs)
       stds = _smooth_(stds)
       cors = _smooth_(cors)
       # lvvcs = _smooth_(lvvcs)
       ax2 = ax.twinx()
       # ax2.plot(frames, covs, linewidth = 0.3, color = "red", zorder = 1)
       # ax2.plot(frames, vels, linewidth = 0.3, color = "purple", zorder = 1)
       # print (len(frames))
       ax2.plot(frames, lvvcs, linewidth = 0.3, color = "red", zorder = 1)
       ax2.set_ylabel("Speed Distribution Coefficient", color = "red")
       
       ax2.set_ylim(0.0, 1.0)
       fig.show()
       
       ''' plot ssd '''
       fig2, ax3 = plt.subplots()
       ax3.scatter(steps, ks, color = "blue", zorder = 3)
       ax3.plot(steps, ks, color = "black", linestyle = "dashed", zorder = 2)
       ax3.scatter(steps, vs, color = "green", zorder = 3)
       ax3.plot(steps, vs, color = "black", linestyle = "dashed", zorder = 2)
       ax3.set_xlabel("Frames (0.1sec)")
       ax3.set_ylabel("Density (veh/mi)", color = "blue")
       ax4 = ax3.twinx()
       ax4.scatter(steps, ssds, color = "orange", zorder = 3)
       ax4.plot(steps, ssds, color = "black", linestyle = "dashed", zorder = 2)
       ax4.set_ylabel("SSD")
       fig2.show()
       
       
       



def __q_k__():

    T = [1118847100000, 1118847100000 + 650000]
    X = [100, 2000]
    
    q = []
    k = []
    
    for i in range(800):
        t = random.randint(T[0], T[1])
        x = random.randint(X[0], X[1])
        print (i)
        print (x, t)
        sec = us101_data[ (us101_data['Local_Y'] <  x + 200) &
                      (us101_data['Local_Y'] >=  x) &
                      (us101_data['Global_Time'] >= t ) &
                      (us101_data['Global_Time'] < 	t + 10000 ) &
                      (us101_data['Lane_ID'] < 6 )            ]
        res = get_elements(sec)
        q.append(res["flow"])
        k.append(res["density"])
    plt.scatter(k, q, color="black", s= 1)
    plt.xlim(0, 150)
    plt.ylim(0, 2500)
    plt.savefig("qk", dpi=300)       
    plt.show()
       
def __mpr__(df):
    g = us101_data.groupby("Vehicle_ID")
    v_num = len(g)
    cv = df[df["Vehicle_ID"].isin(random.sample(set(df["Vehicle_ID"].unique()), int (len(g) * 50 / 100) ))]
    return cv 


   
if __name__ == "__main__":     
    ''' create a new object '''    
    # us101 = cv_data()  
    # us101_data = us101.__Import_Data__()
    # samples = us101.__sample__()
    
    # series = samples[0]["current lvvc series"][0]
     
    ''' import samples to create an object '''
    
    # us101 = cv_data()
    # us101.__import_samples__("./data/sdc_samples_50_17_02_2021_23_45_12")
    # us101_sample = us101.samples
    # sdc_result = us101.__cross_validation__()
      
    
    
   
    
    
 
    # print_sample (us101.samples.values[340])
    # s1  = _smooth_(us101.samples.values[340][3])
    
    
    # t1 = us101_data[ (us101_data['Local_Y'] <  700) &
    #                  (us101_data['Local_Y'] >= 500) &
    #                  (us101_data['Global_Time'] >= 	1118847240000 ) &
    #                  (us101_data['Global_Time'] < 	1118847250000 ) &
    #                  (us101_data['Lane_ID'] < 6 )            ]
    
    # a = get_elements(t1)

    
    # print_sample(samples[0].values[997])
    
    __task__()
    

    
    
    
    
    
    
    
    
    
   
    
    # cv = __mpr__()
    
    # __task__()
    # 
    # se = us101_data[ (us101_data['Local_Y'] <  580) &
    #                       (us101_data['Local_Y'] >=  380) &
    #                       (us101_data['Global_Time'] >= 1118847459464 ) &
    #                       (us101_data['Global_Time'] < 	1118847489464 ) &
    #                       (us101_data['Lane_ID'] < 6 )            ]
    # els = get_elements(se)
    # __q_k__()
            
    
    # us101.__ts__()
    
    
    # byID = us101_data.groupby("Frame_ID")
    # id_5 = byID.get_group(6098)
    # -*- coding: utf-8 -*-

# -*- coding: utf-8 -*-




